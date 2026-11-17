import pytest
from conftest import fixture, page

pytestmark = pytest.mark.anyio

TOOLS = {
    "kl_cities",
    "kl_districts",
    "kl_search_listings",
    "kl_listing",
    "kl_price_stats",
    "kl_neighborhood_prices",
    "kl_agencies",
    "kl_agency",
}


async def test_all_tools_are_read_only_and_described(client):
    tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == TOOLS
    for t in tools:
        assert t.annotations.read_only_hint is True, t.name
        assert t.title and t.description.splitlines()[0].endswith("."), t.name


async def test_cities_filter(client, api):
    api["/api/provinces"] = fixture("provinces.json")
    r = (await client.call_tool("kl_cities", {"query": "تهران", "limit": 2})).structured_content
    assert r["total"] >= 1 and r["cities"][0]["province"] and r["cities"][0]["id"]


async def test_districts(client, api):
    api["/api/regions"] = fixture("regions.json")
    r = (await client.call_tool("kl_districts", {"city_id": 272905, "level": "district"})).structured_content
    assert api.calls[0].url.params["districts"] == "1" and r["rows"][0]["id"]


async def test_search_page1_reads_html(client, api):
    api["/api/search"] = {"url": "/buy/tehran?size=25"}
    api["/buy/tehran"] = page("list_page1.html")
    r = (
        await client.call_tool(
            "kl_search_listings", {"deal": "buy", "city_id": 272905, "max_price": 5, "rooms": 2, "limit": 3}
        )
    ).structured_content
    q = api.calls[0].url.params
    assert q["listingType"] == "BUY" and q["city"] == "272905" and q["priceOrDeposit_LE"] == "5" and q["numBeds"] == "2"
    assert r["total_pages"] == 2247 and r["has_next_page"] and len(r["listings"]) == 3
    first = r["listings"][0]
    assert first["price_toman"] > 0 and first["url"].endswith("/detail/" + first["id"])


async def test_search_page2_uses_api(client, api):
    api["/api/search"] = {"url": "/rent/tehran?size=25"}
    api["/api/listings"] = fixture("listings_p2.json")
    r = (await client.call_tool("kl_search_listings", {"deal": "rent", "page": 2})).structured_content
    q = api.calls[1].url.params
    assert q["url"] == "/rent/tehran?size=25" and q["page"] == "2"
    assert len(r["listings"]) == 3 and r["total_pages"] is None


async def test_listing_detail(client, api):
    api["/detail/3680886"] = page("detail.html")
    r = (await client.call_tool("kl_listing", {"listing_id": "3680886"})).structured_content
    assert r["price_toman"] == 18500000000 and r["area_m2"] == 90 and r["year_built_jalali"] == 1405
    assert r["point"]["lat"] and r["specs"]["elevator"] is True and "$undefined" not in str(r)
    assert "phone" not in r


async def test_listing_missing_is_error(client, api):
    api["/detail/1"] = lambda request: __import__("httpx").Response(404, text="nope")
    r = await client.call_tool("kl_listing", {"listing_id": "1"})
    assert r.is_error


async def test_price_stats(client, api):
    api["/house-prices/tehran-velenjak"] = page("house_prices_velenjak.html")
    r = (await client.call_tool("kl_price_stats", {"area": "tehran-velenjak", "limit": 3})).structured_content
    assert r["area"] == "منطقه 1" and r["period_jalali"] == "1405-08"
    assert r["selected"]["price_per_m2_toman"] == 697323526 and len(r["sub_areas"]) <= 3
    assert len(r["trend"]) == 12 and r["trend"][-1]["period"] == "1405-08"


async def test_neighborhood_prices(client, api):
    api["/api/neighborhoods/finder"] = fixture("finder.json")
    r = (await client.call_tool("kl_neighborhood_prices", {"city_id": 272905, "limit": 5})).structured_content
    prices = [n["price_per_m2_toman"] for n in r["neighborhoods"]]
    assert prices == sorted(prices, reverse=True) and r["period"]


async def test_agencies(client, api):
    api["/api/agency/suggest"] = fixture("agency_suggest.json")
    api["/real-estate-agencies/detail/1001300/a"] = page("agency.html")
    r = (await client.call_tool("kl_agencies", {"name": "مروارید"})).structured_content
    assert r["agencies"][0]["id"] == "1001300"
    r = (await client.call_tool("kl_agency", {"agency_id": "1001300"})).structured_content
    assert r["name"] == "مروارید" and r["active_listings"] > 0 and r["point"]
