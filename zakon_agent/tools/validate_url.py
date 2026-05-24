import httpx


# Vzor URL na zakonyprolidi.cz: "183/2006" → "https://www.zakonyprolidi.cz/cs/2006-183"
# Pokud chceš jiný zdroj zákonů, změň tuto funkci a fetch_zakon.py (parsování HTML se liší)
def build_url(zakon_id: str) -> str:
    cislo, rok = zakon_id.split("/")
    return f"https://www.zakonyprolidi.cz/cs/{rok}-{cislo}"


async def build_and_validate(zakon_id: str) -> dict:
    url = build_url(zakon_id)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=5) as client:
            response = await client.head(url)
            valid = response.status_code == 200
    except httpx.RequestError:
        valid = False
    return {"zakon_id": zakon_id, "url": url, "valid": valid}
