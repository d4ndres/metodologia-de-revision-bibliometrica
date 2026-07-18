from datetime import datetime
import os

import pandas as pd
import pybliometrics
from dotenv import load_dotenv
from pybliometrics.scopus import ScopusSearch, AbstractRetrieval, SerialTitleISSN
from tqdm import tqdm


QUERY = """
TITLE-ABS-KEY ("manual assembl*" OR "industrial assembl*" OR "assembly operation*" OR "assembly task*" OR "assembly process" OR "assembly workstation*") AND ("augmented realit*" OR "mixed realit*" OR "spatial augmented realit*" OR "Augmented Feedback" OR "projected augmented realit*" OR "projection-based augmented realit*" OR "mobile augmented realit*" OR "handheld augmented realit*" OR "in-situ projection" OR "in situ projection") AND ("reduc* cognitive" OR assist* OR guid* OR instruction* OR support* OR feedback OR monitor* OR verif* OR "error detect*" OR "progress recognition" OR "assembly state")
""".strip()
# QUERY = """
# TITLE-ABS-KEY ( "manual assembl*" OR "industrial assembl*" OR "assembly task*" OR "assembly operation*" ) AND ( "assembly monitoring" OR "assembly verification" OR "assembly step recognition" OR "assembly operation recognition" OR "error detection" OR "detect error*" OR "quality assurance" ) AND ( vision OR "computer vision" OR "image*" OR camera* )
# """.strip()

# Fields of interest kept from the ScopusSearch result.
# (The rest of the COMPLETE view fields are dropped to keep the Excel clean;
#  set KEEP_ONLY_SELECTED = False to keep them all.)
KEEP_ONLY_SELECTED = True
SELECTED_COLS = [
    "eid",
    "doi",
    "title",
    "description",           # article abstract / summary (COMPLETE view only)
    "publicationName",       # journal / source name
    "coverDate",             # cover date (publication year is derived from it)
    "citedby_count",         # number of citations of the article
    "aggregationType",       # Journal / Conference Proceeding / Book ...
    "subtypeDescription",    # document type: Article, Review, Conference Paper...
    "affiliation_country",   # affiliation countries (separated by ;)
    "affiliation_city",      # affiliation cities (separated by ;)
    "author_names",          # author names (separated by ;)
    "author_ids",            # author Scopus IDs (separated by ;)
    "author_afids",          # author affiliation IDs (separated by ;)
    "issn",
    "eIssn",
]


def get_year(cover_date) -> int | None:
    """Extract the year (int) from a coverDate like '2024-05-01'."""
    if not cover_date or pd.isna(cover_date):
        return None
    try:
        return int(str(cover_date)[:4])
    except (ValueError, TypeError):
        return None


def fetch_journal_metrics(issn: str, cache: dict) -> dict:
    """Fetch journal quality metrics via SerialTitleISSN (CITESCORE view).

    Returns a dict with the CiteScore list per year, plus SJR and SNIP.
    Caches by ISSN to avoid repeated calls and to save API quota.
    """
    if not issn or pd.isna(issn):
        return {}
    issn = str(issn).replace("-", "").strip()
    if issn in cache:
        return cache[issn]

    metrics: dict = {}
    try:
        st = SerialTitleISSN(issn, view="CITESCORE", refresh=True)
        # CiteScore per year, with percentile and rank per subject category
        metrics["citescore_by_year"] = {
            row.year: {
                "citescore": row.citescore,
                # take the best percentile across all categories of the year
                "percentile": max((r.percentile for r in (row.rank or [])), default=None),
                "rank": min((r.rank for r in (row.rank or [])), default=None),
            }
            for row in (st.citescoreyearinfolist or [])
        }
        # SJR and SNIP: the API only returns the most recent year's value
        # (no history), unlike CiteScore which is available per year.
        metrics["sjr_by_year"] = {yr: val for yr, val in (st.sjrlist or [])}
        metrics["snip_by_year"] = {yr: val for yr, val in (st.sniplist or [])}
    except Exception as exc:  # noqa: BLE001
        print(f"  [warning] Could not fetch metrics for ISSN {issn}: {exc}")

    cache[issn] = metrics
    return metrics


def percentile_to_quartile(percentile) -> str | None:
    """Convert a percentile (0-100) to its corresponding quartile (Q1-Q4)."""
    if percentile is None or pd.isna(percentile):
        return None
    p = float(percentile)
    if p >= 75:
        return "Q1"
    if p >= 50:
        return "Q2"
    if p >= 25:
        return "Q3"
    return "Q4"


def enrich_with_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Add journal quality metric columns to each article.

    Matches CiteScore/percentile/SJR/SNIP with the article's publication year;
    if that year has no data, falls back to the most recent year available.
    """
    cache: dict = {}
    citescores, percentiles, quartiles, sjrs, snips = [], [], [], [], []

    # Number of unique journals (actual API calls) to inform the user
    unique_issns = {
        str(i).replace("-", "").strip()
        for i in pd.concat([df.get("issn"), df.get("eIssn")]).dropna()
    }
    print(f"  Unique journals to query: {len(unique_issns)} "
          f"(over {len(df)} articles)", flush=True)

    progress = tqdm(total=len(df), desc="Journal metrics", unit="art")
    for _, row in df.iterrows():
        issn = row.get("issn") or row.get("eIssn")
        year = get_year(row.get("coverDate"))

        journal = str(row.get("publicationName") or "?")[:45]
        progress.set_postfix_str(journal)

        m = fetch_journal_metrics(issn, cache)

        cs_by_year = m.get("citescore_by_year", {})
        sjr_by_year = m.get("sjr_by_year", {})
        snip_by_year = m.get("snip_by_year", {})

        def pick(mapping: dict, yr):
            """Take the value for the requested year; else the most recent one."""
            if not mapping:
                return None
            if yr in mapping:
                return mapping[yr]
            return mapping[max(mapping)]

        cs_entry = pick(cs_by_year, year) or {}
        citescores.append(cs_entry.get("citescore"))
        percentiles.append(cs_entry.get("percentile"))
        quartiles.append(percentile_to_quartile(cs_entry.get("percentile")))
        sjrs.append(pick(sjr_by_year, year))
        snips.append(pick(snip_by_year, year))
        progress.update(1)

    progress.close()

    df = df.copy()
    df["pub_year"] = df["coverDate"].apply(get_year)
    df["citescore"] = citescores
    df["citescore_percentile"] = percentiles
    df["citescore_quartile"] = quartiles
    df["sjr"] = sjrs
    df["snip"] = snips
    return df


def main() -> None:
    # Load variables from the .env file
    load_dotenv()

    # Get the main Scopus key from the environment
    api_key = os.getenv("ScopusSecretKey")

    if api_key:
        pybliometrics.init(keys=[api_key])
    else:
        pybliometrics.init()

    # Run the search
    search = ScopusSearch(
        QUERY,
        subscriber=True,
        view="COMPLETE",
        refresh=True,
        download=True,
        verbose=True,
    )

    results = search.results or []
    df = pd.DataFrame([result._asdict() for result in results])

    # Drop duplicates if the eid column exists
    if not df.empty and "eid" in df.columns:
        df = df.drop_duplicates(subset=["eid"])

    # Keep only the fields of interest (optional)
    if KEEP_ONLY_SELECTED and not df.empty:
        cols = [c for c in SELECTED_COLS if c in df.columns]
        df = df[cols]

    # Enrich with journal quality metrics (CiteScore, percentile, quartile,
    # SJR, SNIP) via SerialTitleISSN. Cached by ISSN.
    if not df.empty:
        print("Fetching journal quality metrics (CiteScore/SJR/SNIP)...")
        df = enrich_with_metrics(df)

    # Add the search string used, as the last column of every row
    if not df.empty:
        df["string_search"] = QUERY

    # Export to Excel
    filename = f"scopus_results_{datetime.now():%Y%m%d_%H%M}.xlsx"
    df.to_excel(filename, index=False)

    print(f"Results found: {len(df)}")
    print(f"File generated: {filename}")

    # Show a simple preview
    preview_cols = [
        col
        for col in ["title", "pub_year", "citedby_count", "citescore",
                    "citescore_percentile", "citescore_quartile", "sjr"]
        if col in df.columns
    ]
    if preview_cols:
        print(df[preview_cols].head())


if __name__ == "__main__":
    main()
