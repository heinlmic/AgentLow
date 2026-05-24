import re

# Hledá "zákon č. X/RRRR Sb." nebo "vyhláška č. X/RRRR Sb." — rozšiř vzor pokud chybí nějaký typ odkazu
ODKAZ_PATTERN = re.compile(
    r'(?:zákon|vyhláška|nařízení)[a-z\s]*č\.\s*(\d+/\d{4})\s*Sb', re.IGNORECASE
)
# Hledá řádky začínající "§ 123" nebo "§123a"
PARAGRAF_PATTERN = re.compile(r'^§\s*(\d+\w*)\s*(.*?)$', re.MULTILINE)


def structure_zakon(text: str) -> dict:
    paragrafy = {}
    for match in PARAGRAF_PATTERN.finditer(text):
        cislo = f"§{match.group(1)}"
        nadpis = match.group(2).strip()
        paragrafy[cislo] = nadpis

    odkazy = sorted(set(ODKAZ_PATTERN.findall(text)))

    seznam_paragrafu = ", ".join(
        f"{k} {v}" if v else k for k, v in list(paragrafy.items())[:50]
    )

    return {
        "paragrafy": paragrafy,
        "odkazy": odkazy,
        "seznam_paragrafu": seznam_paragrafu,
    }
