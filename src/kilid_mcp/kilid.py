"""Kilid tools: cities, districts, listing search and detail, house-price statistics, agencies."""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal
from urllib.parse import quote

from pydantic import Field

from .http import SITE, ApiError, fetch
from .registry import tool

DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
PUSH = re.compile(r'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', re.S)
PROPERTY_TYPES = Literal[
    "APARTMENT_TOWER", "VILLA_GARDEN", "PENT_HOUSE", "OFFICE", "STORE", "STORAGE_WORKSHOP_FACTORY", "FARM", "LAND",
    "REAL_ESTATE",
]  # fmt: skip
SORTS = Literal["newest", "cheapest", "priciest", "largest", "smallest"]
SORT_WIRE = {
    "newest": "searchDate_desc",
    "cheapest": "priceOrDeposit_asc",
    "priciest": "priceOrDeposit_desc",
    "largest": "floorArea_desc",
    "smallest": "floorArea_asc",
}


def _ascii(v: Any) -> Any:
    """Persian digits to ASCII in site text, recursively."""
    if isinstance(v, str):
        return v.translate(DIGITS)
    if isinstance(v, list):
        return [_ascii(x) for x in v]
    if isinstance(v, dict):
        return {k: _ascii(x) for k, x in v.items()}
    return v


def _clean(v: Any) -> Any:
    """Next.js flight data marks undefined as the string "$undefined"; make it None."""
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return None if v == "$undefined" else v


def _flight(page: str) -> str:
    """The page's Next.js flight payload (the data its React tree was rendered from) as one string."""
    return "".join(json.loads(p) for p in PUSH.findall(page))


def _decode(flight: str, marker: str, skip: int | None = None) -> Any:
    """Decode the JSON value that follows `marker` in the flight payload (or starts `skip` chars into it)."""
    i = flight.find(marker)
    if i < 0:
        return None
    try:
        return _clean(json.JSONDecoder().raw_decode(flight[i + (len(marker) if skip is None else skip) :])[0])
    except ValueError:
        return None


def _m2(v: Any) -> int | None:
    """Round a Toman amount to a whole number."""
    return round(v) if isinstance(v, int | float) else None


def _card(x: dict) -> dict:
    """One listing card (search result) trimmed."""
    loc = x.get("location") or {}
    ag = x.get("agency") or {}
    return {
        "id": x["id"],
        "title": _ascii(x.get("title")),
        "deal": (x.get("listingType") or "").lower(),
        "location": loc.get("label"),
        "location_id": loc.get("id"),
        "price_toman": x.get("priceToman"),  # sale price, or the deposit (rahn) of a rental
        "rent_toman": x.get("rentToman"),  # monthly rent, rentals only
        "agreed_price": x.get("agreedPrice") or None,
        "featured": x.get("featured") or None,  # paid promotion; may ignore the search filters
        "area_m2": x.get("areaM2"),
        "rooms": x.get("rooms"),
        "year_built_jalali": x.get("yearBuilt"),
        "agency": ag.get("name"),
        "agency_id": ag.get("id"),
        "publisher": (x.get("publisherKind") or "").lower() or None,
        "published_at": x.get("publishedAt"),
        "image": (x.get("coverImage") or {}).get("url"),
        "url": f"{SITE}/detail/{x['id']}",
    }


@tool("Cities")
async def kl_cities(
    query: Annotated[
        str | None,
        Field(description="Part of a city or province name in Persian (e.g. 'کرج', 'گیلان'). Empty lists every city."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Max cities returned.")] = 30,
) -> dict:
    """Find the city id and slug Kilid uses for a city (needed by kl_search_listings and kl_districts).

    Covers every city Kilid lists, grouped by province. `has_districts` tells whether kl_districts has data.
    """
    data = await fetch("/api/provinces")
    q = (query or "").strip()
    cities = [
        {
            "id": c["id"],
            "slug": c["slug"],
            "name": c["name"],
            "province": p["name"],
            "has_districts": c.get("haveRegion"),
        }
        for p in data
        for c in p["cities"]
        if not q or q in c["name"] or q in p["name"]
    ]
    return {"total": len(cities), "cities": cities[:limit]}


@tool("Districts")
async def kl_districts(
    city_id: Annotated[int, Field(description="City id from kl_cities (Tehran is 272905).")],
    level: Annotated[
        Literal["district", "neighborhood"],
        Field(description="district = municipal regions (منطقه 1..22 in Tehran); neighborhood = محله level."),
    ] = "district",
    query: Annotated[str | None, Field(description="Part of a name in Persian, e.g. 'ولنجک'.")] = None,
    limit: Annotated[int, Field(ge=1, le=200, description="Max rows returned.")] = 50,
) -> dict:
    """List the districts or neighborhoods of a city with the ids kl_search_listings takes as region_ids.

    Neighborhood rows carry the slug used by kl_price_stats (e.g. tehran-velenjak) and their municipal region.
    """
    params = {"cityId": city_id} | ({"districts": 1} if level == "district" else {})
    rows = await fetch("/api/regions", params)
    q = (query or "").strip()
    out = [
        {
            "id": r["id"],
            "name": r["name"],
            "slug": r.get("slug"),
            "municipal_region": r.get("municipalArea") or r.get("districtOutline"),
        }
        for r in rows
        if not q or q in r["name"] or q in " ".join(r.get("tags") or [])
    ]
    return {"total": len(out), "rows": out[:limit]}


@tool("Search listings")
async def kl_search_listings(
    deal: Annotated[
        Literal["buy", "rent"], Field(description="buy = for sale, rent = rental (rahn and monthly rent).")
    ] = "buy",
    city_id: Annotated[int | None, Field(description="City id from kl_cities. Omit for all of Iran.")] = None,
    region_ids: Annotated[
        list[int] | None, Field(description="District or neighborhood ids from kl_districts.")
    ] = None,
    property_type: Annotated[list[PROPERTY_TYPES] | None, Field(description="Property types; omit for all.")] = None,
    min_price: Annotated[
        int | None, Field(ge=0, description="Sale price floor in Toman; for rent, the deposit floor.")
    ] = None,
    max_price: Annotated[
        int | None, Field(ge=0, description="Sale price cap in Toman; for rent, the deposit cap.")
    ] = None,
    min_rent: Annotated[int | None, Field(ge=0, description="Monthly rent floor in Toman (rent only).")] = None,
    max_rent: Annotated[int | None, Field(ge=0, description="Monthly rent cap in Toman (rent only).")] = None,
    min_area: Annotated[int | None, Field(ge=0, description="Floor area floor in m2.")] = None,
    max_area: Annotated[int | None, Field(ge=0, description="Floor area cap in m2.")] = None,
    rooms: Annotated[int | None, Field(ge=0, le=10, description="Exact bedroom count (0 = studio).")] = None,
    min_rooms: Annotated[int | None, Field(ge=0, le=10, description="At least this many bedrooms.")] = None,
    sort: Annotated[SORTS, Field(description="Result order.")] = "newest",
    page: Annotated[int, Field(ge=1, le=200, description="Page number, 25-28 listings per page.")] = 1,
    limit: Annotated[int, Field(ge=1, le=30, description="Max listings returned from the page.")] = 15,
) -> dict:
    """Search Kilid sale and rental listings with price, area, room and place filters.

    Prices are Toman. Rentals carry price_toman = deposit and rent_toman = monthly rent. Get ids from kl_cities and
    kl_districts, then open one result with kl_listing. `url` in the answer is the Kilid search page. Featured
    (paid) listings can appear on top even when they fall outside the filters; rent_toman 0 means deposit-only.
    """
    wire: dict[str, Any] = {"listingType": deal.upper()}
    for key, val in {
        "city": city_id,
        "region_IN": ",".join(map(str, region_ids)) if region_ids else None,
        "propertyType_IN": ",".join(property_type) if property_type else None,
        "priceOrDeposit_GE": min_price,
        "priceOrDeposit_LE": max_price,
        "unitPriceOrRent_GE": min_rent if deal == "rent" else None,
        "unitPriceOrRent_LE": max_rent if deal == "rent" else None,
        "floorArea_GE": min_area,
        "floorArea_LE": max_area,
        "numBeds": rooms,
        "numBeds_GE": min_rooms,
        "sort": None if sort == "newest" else SORT_WIRE[sort],
    }.items():
        if val is not None:
            wire[key] = val
    # /api/search turns the filters into the canonical listing-page path (the page the site would open).
    path = (await fetch("/api/search", wire))["url"]
    if page == 1:
        # The API refuses page 1; the first page only exists inside the server-rendered HTML.
        flight = _flight(await fetch(path, None, "html"))
        items = _decode(flight, '"initial":[{"id"', len('"initial":')) or []
        pages = _decode(flight, '"totalPages":')
        more = bool(_decode(flight, '"hasNextPage":'))
    else:
        data = await fetch("/api/listings", {"url": path, "page": page})
        items, more, pages = data.get("items", []), bool(data.get("hasNextPage")), None
    return {
        "url": SITE + path,
        "page": page,
        "total_pages": pages,
        "has_next_page": more,
        "listings": [_card(x) for x in items[:limit]],
    }


@tool("Listing detail")
async def kl_listing(
    listing_id: Annotated[
        str,
        Field(
            description="Listing id (the number in kilid.com/detail/<id>), from kl_search_listings.",
            pattern=r"^\d{1,12}$",
        ),
    ],
) -> dict:
    """Get one Kilid listing: price, area, rooms, floor, facilities, description, location point and agency.

    Prices are Toman. The advertiser's phone number is hidden behind a button on the site and is not returned.
    """
    flight = _flight(await fetch(f"/detail/{listing_id}", None, "html"))
    x = _decode(flight, f'{{"id":"{listing_id}","title"', 0)
    if not x:
        raise ApiError(f"Listing {listing_id} has no readable data (removed or archived?).")
    specs = {k: v for k, v in (x.get("specs") or {}).items() if v not in (None, [], "")}
    return {
        **_card(x),
        "year_built_jalali": x.get("yearBuilt") or (x.get("specs") or {}).get("yearBuilt"),
        "description": _ascii(x.get("description")),
        "property_type": x.get("propertyType"),
        "archived": x.get("archived") or None,
        "specs": specs,  # floor, parking, elevator, storage... as shown on the page
        "features": x.get("features"),
        "point": x.get("point"),
        "breadcrumbs": [b["title"] for b in x.get("breadcrumbs") or []],
        "photos": len(x.get("images") or []) or None,
    }


@tool("House price statistics")
async def kl_price_stats(
    area: Annotated[
        str,
        Field(
            description="City slug (tehran, karaj, ...) or area slug from kl_districts (tehran-region1, tehran-velenjak)."
        ),
    ] = "tehran",
    limit: Annotated[int, Field(ge=1, le=60, description="Max sub-areas listed.")] = 25,
) -> dict:
    """Get median price per m2 for a city or area from Kilid's market report, with sub-areas and a 12-month trend.

    Prices are Toman per m2 from asking prices of listings. For a neighborhood slug, `area` is its parent
    district and `selected` is the neighborhood's own row.
    """
    flight = _flight(await fetch(f"/house-prices/{quote(area)}", None, "html"))
    comp = _decode(flight, '"comparison":')
    if not isinstance(comp, dict):
        raise ApiError(f"No price report for '{area}'. Use a city slug or a slug from kl_districts.")
    series = _decode(flight, '"series":') or {}

    def row(r: dict) -> dict:
        return {
            "name": r.get("name"),
            "slug": r.get("slug"),
            "price_per_m2_toman": _m2(r.get("pricePsmMedian")),
            "median_total_price_toman": _m2(r.get("priceTotalMedian")),
            "rent_per_m2_toman": _m2(r.get("rentEffPsmMedian")),
            "median_area_m2": r.get("medianFloorArea"),
            "median_age_years": r.get("medianAge"),
            "month_change_pct": r.get("momPct"),
            "year_change_pct": r.get("yoyPct"),
            "sample_size": r.get("sampleSize"),
            "reliable": r.get("trust") == "DIRECT",
        }

    rows = comp.get("rows") or []
    return {
        "area": comp["area"]["nameFa"],
        "period_jalali": comp.get("periodCode"),
        "summary": row(comp["total"]),
        "selected": next((row(r) for r in rows if r.get("slug") == area), None),
        "sub_areas": [row(r) for r in rows if r.get("pricePsmMedian")][:limit],
        "trend": [
            {"period": p["periodCode"], "price_per_m2_toman": _m2(p["value"])}
            for p in (series.get("total") or {}).get("points") or []
        ],
    }


@tool("Neighborhood prices")
async def kl_neighborhood_prices(
    city_id: Annotated[int, Field(description="City id from kl_cities (Tehran is 272905).")],
    district: Annotated[
        int | None, Field(description="Municipal region number to keep, e.g. 1 for منطقه 1 (Tehran).")
    ] = None,
    sort: Annotated[Literal["priciest", "cheapest"], Field(description="Order by price per m2.")] = "priciest",
    limit: Annotated[int, Field(ge=1, le=100, description="Max neighborhoods returned.")] = 25,
) -> dict:
    """Rank a city's neighborhoods by median price per m2 (Toman), from Kilid's neighborhood guide.

    `quality` is RELIABLE, ESTIMATED or SUSPECT (thin data); neighborhoods without data are left out.
    """
    data = await fetch("/api/neighborhoods/finder", {"cityId": city_id})
    rows = [
        {
            "name": r["name"],
            "region_id": r["regionId"],
            "municipal_region": r.get("municipalAreaNum"),
            "price_per_m2_toman": _m2(r["pricePsm"]),
            "quality": r.get("quality"),
            "guide": SITE + r["path"] if r.get("linkable") else None,
        }
        for r in data["rows"]
        if r.get("pricePsm") and (district is None or r.get("municipalAreaNum") == district)
    ]
    rows.sort(key=lambda r: r["price_per_m2_toman"], reverse=sort == "priciest")
    return {"period": (data.get("period") or {}).get("periodLabel"), "total": len(rows), "neighborhoods": rows[:limit]}


@tool("Find agencies")
async def kl_agencies(
    name: Annotated[str, Field(min_length=2, description="Part of the agency name in Persian.")],
    city_id: Annotated[int | None, Field(description="Limit to one city (id from kl_cities).")] = None,
) -> dict:
    """Search real-estate agencies registered on Kilid by name; open one with kl_agency.

    Returns up to a few matches with their district. Phone numbers are not public.
    """
    data = await fetch("/api/agency/suggest", {"name": name} | ({"city": city_id} if city_id else {}))
    return {
        "agencies": [
            {"id": r["id"], "name": r["name"], "city": r.get("cityName"), "district": r.get("regionName")}
            for r in data.get("rows", [])
        ]
    }


@tool("Agency detail")
async def kl_agency(
    agency_id: Annotated[
        str, Field(description="Agency id from kl_agencies or a listing's agency_id.", pattern=r"^\d{1,12}$")
    ],
) -> dict:
    """Get a Kilid real-estate agency: address, district, location point, active listings and agent count."""
    flight = _flight(await fetch(f"/real-estate-agencies/detail/{agency_id}/a", None, "html"))
    marker = f'"agency":{{"id":"{agency_id}","name"'
    a = _decode(flight, marker, len('"agency":'))
    if not a:
        raise ApiError(f"Agency {agency_id} has no readable data.")
    return {
        "id": a["id"],
        "name": a["name"],
        "title": a.get("title"),
        "address": _ascii(a.get("address")),
        "city": (a.get("city") or {}).get("name"),
        "district": (a.get("region") or {}).get("name"),
        "point": a.get("point"),
        "active_listings": a.get("activeListingCount"),
        "agents": a.get("agentCount"),
        "website": a.get("websiteUrl"),
        "logo": a.get("logoUrl"),
        "url": f"{SITE}/real-estate-agencies/detail/{a['id']}/{quote(a['name'])}",
    }
