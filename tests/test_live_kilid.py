import pytest

pytestmark = [pytest.mark.live, pytest.mark.anyio]

TEHRAN = 272905


async def test_live_cities_districts(client):
    r = (await client.call_tool("kl_cities", {"query": "کرج"})).structured_content
    assert r["cities"][0]["slug"] == "karaj"
    r = (
        await client.call_tool("kl_districts", {"city_id": TEHRAN, "level": "neighborhood", "query": "ولنجک"})
    ).structured_content
    assert r["rows"][0]["slug"] == "tehran-velenjak"


async def test_live_search_and_listing(client):
    args = {
        "deal": "buy",
        "city_id": TEHRAN,
        "property_type": ["APARTMENT_TOWER"],
        "min_area": 60,
        "max_area": 100,
        "limit": 5,
    }
    r = (await client.call_tool("kl_search_listings", args)).structured_content
    assert r["total_pages"] and r["listings"]
    assert all(60 <= x["area_m2"] <= 100 for x in r["listings"] if x["area_m2"] and not x.get("featured"))
    r2 = (await client.call_tool("kl_search_listings", args | {"page": 2})).structured_content
    assert r2["listings"] and r2["listings"][0]["id"] != r["listings"][0]["id"]
    d = (await client.call_tool("kl_listing", {"listing_id": r["listings"][0]["id"]})).structured_content
    assert d["price_toman"] and d["point"]


async def test_live_rent(client):
    r = (
        await client.call_tool(
            "kl_search_listings",
            {"deal": "rent", "city_id": TEHRAN, "min_rent": 1_000_000, "max_rent": 20_000_000, "limit": 5},
        )
    ).structured_content
    assert r["listings"] and all(0 < x["rent_toman"] <= 20_000_000 for x in r["listings"])


async def test_live_stats(client):
    r = (await client.call_tool("kl_price_stats", {"area": "tehran", "limit": 5})).structured_content
    assert r["summary"]["price_per_m2_toman"] > 10_000_000 and len(r["trend"]) >= 6
    r = (await client.call_tool("kl_neighborhood_prices", {"city_id": TEHRAN, "limit": 5})).structured_content
    assert len(r["neighborhoods"]) == 5


async def test_live_agencies(client):
    r = (await client.call_tool("kl_agencies", {"name": "مروارید"})).structured_content
    assert r["agencies"]
    a = (await client.call_tool("kl_agency", {"agency_id": r["agencies"][0]["id"]})).structured_content
    assert a["name"] and a["address"]
