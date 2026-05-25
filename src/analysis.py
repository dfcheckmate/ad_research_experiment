"""
Causal Analysis
---------------
Loads ad observations from PostgreSQL and estimates the causal effect of
ZIP condition (treatment) on ad exposure.

Statistical models:
  1. Logistic regression  – P(ad exposure | ZIP)
  2. Chi-square test      – independence of ad domain × ZIP condition
  3. Top-domain breakdown – which advertisers differ most across ZIP conditions

Usage:
    python analysis.py [--output results/]
"""

from __future__ import annotations

import argparse
import asyncio
import os

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy.stats import chi2_contingency

from config import DB_URL

USE_SQLITE = DB_URL.startswith("sqlite")
SQLITE_PATH = DB_URL.removeprefix("sqlite:///") if USE_SQLITE else None

sns.set_theme(style="whitegrid")


# ── Load data ─────────────────────────────────────────────────────────────────


async def load_data() -> pd.DataFrame:
    if USE_SQLITE:
        import aiosqlite

        async with aiosqlite.connect(SQLITE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            try:
                async with conn.execute(
                    "SELECT trial_id, zip_condition, ad_url, ad_domain, "
                    "ad_network, measurement_site, source_type, intent_profile, query_topic, search_query, "
                    "ad_headline, ad_description, advertiser_name, landing_url, landing_domain, "
                    "inferred_topic, observed_at "
                    "FROM ad_observations ORDER BY observed_at"
                ) as cursor:
                    rows = await cursor.fetchall()
            except Exception as e:
                # A fresh SQLite file may exist but not have schema initialized.
                if "no such table" in str(e).lower():
                    return pd.DataFrame(
                        columns=[
                            "trial_id",
                            "zip_condition",
                            "ad_url",
                            "ad_domain",
                            "ad_network",
                            "measurement_site",
                            "source_type",
                            "intent_profile",
                            "query_topic",
                            "search_query",
                            "ad_headline",
                            "ad_description",
                            "advertiser_name",
                            "landing_url",
                            "landing_domain",
                            "inferred_topic",
                            "observed_at",
                        ]
                    )
                raise
        return pd.DataFrame([dict(r) for r in rows])

    import asyncpg

    conn = await asyncpg.connect(DB_URL)
    rows = await conn.fetch(
        """
        SELECT trial_id, zip_condition, ad_url, ad_domain,
               ad_network, measurement_site, source_type, intent_profile, query_topic, search_query,
               ad_headline, ad_description, advertiser_name, landing_url, landing_domain,
               inferred_topic, observed_at
        FROM ad_observations ORDER BY observed_at
        """
    )
    await conn.close()
    return pd.DataFrame([dict(r) for r in rows])


# ── Pre-processing ────────────────────────────────────────────────────────────


def preprocess(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # Treatment label — kept as a categorical string for multi-level models.
    # is_rich is retained for backward compatibility with legacy 2-condition data.
    df["is_rich"] = (df["zip_condition"] == "rich_zip").astype(int)
    # Binary outcome: 1 observation = 1 ad shown (already filtered upstream)
    df["ad_shown"] = 1
    # Trial-level counts
    df["ad_domain"] = df["ad_domain"].fillna("unknown")
    for col in [
        "ad_url",
        "source_type",
        "intent_profile",
        "query_topic",
        "search_query",
        "ad_headline",
        "ad_description",
        "advertiser_name",
        "landing_url",
        "landing_domain",
        "inferred_topic",
        "page_title",
        "page_url",
        "screenshot_path",
        "dom_snippet",
    ]:
        if col not in df:
            df[col] = ""
        else:
            df[col] = df[col].fillna("")

    df["request_role"] = df.apply(classify_request_role, axis=1)
    df["platform_class"] = df.apply(classify_platform_class, axis=1)
    df["publisher_context"] = (
        df["measurement_site"].map(infer_publisher_context).fillna("general news")
    )
    df["clean_topic_label"] = df.apply(build_clean_topic_label, axis=1)
    df["final_taxonomy"] = df.apply(classify_final_taxonomy, axis=1)
    return df


def infer_publisher_context(site: str) -> str:
    site = (site or "").lower()
    mapping = {
        "cnn.com": "news & politics",
        "forbes.com": "business & investing",
        "nytimes.com": "news & culture",
        "huffpost.com": "news & lifestyle",
        "usatoday.com": "general news",
        "google_search": "search results",
    }
    for key, label in mapping.items():
        if key in site:
            return label
    return "general news"


def classify_request_role(row: pd.Series) -> str:
    url = (row.get("ad_url") or "").lower()
    domain = (row.get("ad_domain") or "").lower()

    rules = [
        (("beacon.js", "/b?", "scorecardresearch"), "analytics beacon"),
        (("gpt.js", "adsbygoogle.js"), "ad loader script"),
        (("getconfig/sodar", "sodar"), "viewability / anti-fraud config"),
        (
            ("cookie/put", "usersync", "obusersync", "cm.g.doubleclick.net"),
            "identity sync",
        ),
        (("obpixelframe", "pixel", "match.adsrvr.org"), "tracking pixel / retargeting"),
        (("pubads.g.doubleclick.net",), "ad serving request"),
        (
            (
                "appnexus",
                "adnxs",
                "openx",
                "pubmatic",
                "rubiconproject",
                "criteo",
                "amazon-adsystem",
            ),
            "programmatic exchange call",
        ),
        (
            ("outbrain.js", "widgets.outbrain.com", "nanoWidget", "module/"),
            "native recommendation widget",
        ),
    ]

    for needles, label in rules:
        if any(n.lower() in url or n.lower() in domain for n in needles):
            return label
    return "unclassified ad-tech request"


def classify_platform_class(row: pd.Series) -> str:
    domain = (row.get("ad_domain") or "").lower()
    role = row.get("request_role", "")
    if "outbrain" in domain:
        return "native recommendation network"
    if any(
        x in domain for x in ["doubleclick", "googlesyndication", "googleadservices"]
    ):
        return "google display stack"
    if any(
        x in domain
        for x in [
            "adnxs",
            "appnexus",
            "openx",
            "pubmatic",
            "rubiconproject",
            "criteo",
            "adsrvr",
            "amazon-adsystem",
        ]
    ):
        return "programmatic ad exchange"
    if "scorecardresearch" in domain:
        return "audience measurement"
    if "identity" in role:
        return "identity / sync layer"
    return "other ad-tech infrastructure"


def build_clean_topic_label(row: pd.Series) -> str:
    inferred = (row.get("inferred_topic") or "").strip()
    if inferred:
        return inferred

    platform = row.get("platform_class", "")
    role = row.get("request_role", "")
    context = row.get("publisher_context", "")

    if platform == "native recommendation network":
        return f"native recommendations on {context}"
    if platform == "google display stack":
        if role == "ad serving request":
            return f"google display ad serving on {context}"
        if role == "ad loader script":
            return "google display loader"
        return "google display infrastructure"
    if platform == "programmatic ad exchange":
        return f"programmatic display on {context}"
    if platform == "audience measurement":
        return "audience measurement / analytics"
    if role == "identity sync":
        return "identity sync / cross-site matching"
    if role == "tracking pixel / retargeting":
        return "tracking / retargeting pixel"
    return f"ad-tech infrastructure on {context}"


def classify_final_taxonomy(row: pd.Series) -> str:
    role = (row.get("request_role") or "").lower()
    platform = (row.get("platform_class") or "").lower()

    if "identity" in role or "identity" in platform:
        return "identity sync"
    if "measurement" in role or "measurement" in platform or "analytics beacon" in role:
        return "measurement"
    if "retargeting" in role or "tracking pixel" in role:
        return "retargeting"
    if "native recommendation" in platform or "native recommendation widget" in role:
        return "native ad"
    return "display ad"


# ── Model 1 – Logistic regression: ZIP → ad exposure ─────────────────────────


def logistic_regression(df: pd.DataFrame) -> None:
    """
    Poisson GLM: ad_count ~ C(zip_condition).
    Works for any number of proxy identity levels (2 legacy or 3 residential).
    The reference level is set to the alphabetically first identity label.
    """
    # Aggregate: one row per (trial × identity)
    agg = (
        df.groupby(["trial_id", "zip_condition"])["ad_shown"]
        .sum()
        .reset_index(name="ad_count")
    )
    agg["exposed"] = (agg["ad_count"] > 0).astype(int)

    identities = sorted(agg["zip_condition"].unique())
    ref = identities[0]  # alphabetical reference level

    print("\n" + "=" * 60)
    print("MODEL 1 — Proxy identity effect on ad volume")
    print(f"          Reference level: {ref}")
    print("=" * 60)

    # Poisson fallback when exposure is constant (nearly always the case).
    if agg["exposed"].nunique() < 2:
        print("[note] Exposure is constant; using Poisson GLM on ad counts.")
        model = smf.glm(
            f'ad_count ~ C(zip_condition, Treatment(reference="{ref}"))',
            data=agg,
            family=sm.families.Poisson(),
        ).fit()
    else:
        model = smf.glm(
            f'ad_count ~ C(zip_condition, Treatment(reference="{ref}"))',
            data=agg,
            family=sm.families.Poisson(),
        ).fit()

    print(model.summary2())

    import math

    print("\nRate ratios vs reference identity:")
    any_sig = False
    for param, coef in model.params.items():
        if "zip_condition" in param:
            pval = model.pvalues[param]
            rr = math.exp(coef)
            sig = " ← p<0.05" if pval < 0.05 else ""
            print(f"  {param:55s}  β={coef:+.4f}  RR={rr:.4f}  p={pval:.4f}{sig}")
            if pval < 0.05:
                any_sig = True

    if any_sig:
        print("→ At least one identity level significantly predicts ad volume (α=0.05)")
    else:
        print("→ No significant identity effect detected (α=0.05)")


# ── Model 2 – Chi-square: ad domain × ZIP condition ──────────────────────────


def chi_square_test(df: pd.DataFrame) -> None:
    top_domains = df["ad_domain"].value_counts().head(20).index
    sub = df[df["ad_domain"].isin(top_domains)]

    contingency = pd.crosstab(sub["zip_condition"], sub["ad_domain"])
    chi2, p, dof, _ = chi2_contingency(contingency)

    print("\n" + "=" * 60)
    print("MODEL 2 — Chi-square: ad domain distribution × ZIP condition")
    print("=" * 60)
    print(f"χ²({dof}) = {chi2:.4f},  p = {p:.6f}")
    if p < 0.05:
        print("→ Ad domain distribution differs significantly across ZIP conditions")
    else:
        print("→ No significant difference in domain distribution")


# ── Model 3 – Domain breakdown ────────────────────────────────────────────────


def domain_breakdown(df: pd.DataFrame, output_dir: str) -> None:
    top_domains = df["ad_domain"].value_counts().head(20).index
    sub = df[df["ad_domain"].isin(top_domains)]

    pivot = (
        sub.groupby(["ad_domain", "zip_condition"])["ad_shown"]
        .sum()
        .unstack(fill_value=0)
    )

    # Normalise to proportions per condition
    pivot_pct = pivot.div(pivot.sum(axis=0), axis=1) * 100

    print("\n" + "=" * 60)
    print("MODEL 3 — Ad domain share by proxy identity (%)")
    print("=" * 60)
    print(pivot_pct.round(2).to_string())

    # Sort by total share (sum across all identities)
    sort_col = pivot_pct.columns[0]
    palette = ["#d7191c", "#2b83ba", "#1a9641", "#fdae61", "#984ea3"]

    # Plot
    fig, ax = plt.subplots(figsize=(12, 6))
    pivot_pct.sort_values(sort_col, ascending=False).plot(
        kind="barh", ax=ax, color=palette[: len(pivot_pct.columns)]
    )
    ax.set_xlabel("Share of ad impressions (%)")
    ax.set_title("Ad domain distribution by proxy identity")
    ax.legend(title="Proxy identity")
    plt.tight_layout()
    path = os.path.join(output_dir, "domain_distribution.png")
    fig.savefig(path, dpi=150)
    print(f"\n[plot] saved → {path}")
    plt.close(fig)


# ── Model 4 – Odds ratio per domain ──────────────────────────────────────────


def per_domain_odds(df: pd.DataFrame, output_dir: str) -> None:
    """
    For each top domain, fit a Poisson GLM: domain_share ~ C(zip_condition).
    Report the coefficient (log rate-ratio) for each non-reference identity.
    Falls back to logistic for binary data; uses Poisson for multi-level.
    """
    identities = sorted(df["zip_condition"].unique())
    ref = identities[0]
    multi = len(identities) > 2

    records = []
    top_domains = df["ad_domain"].value_counts().head(20).index

    for domain in top_domains:
        sub = df.copy()
        sub["target"] = (sub["ad_domain"] == domain).astype(int)
        if sub["target"].sum() < 10:
            continue
        try:
            formula = f'target ~ C(zip_condition, Treatment(reference="{ref}"))'
            m = smf.logit(formula, data=sub).fit(disp=False)
            # Report the largest-magnitude coefficient and its p-value
            params = {k: v for k, v in m.params.items() if "zip_condition" in k}
            if not params:
                continue
            # Pick the param with the highest absolute effect
            best_param = max(params, key=lambda k: abs(params[k]))
            odds = params[best_param]
            p = m.pvalues[best_param]
            label = best_param.split("T.")[-1].rstrip("]")
            records.append(
                {"domain": domain, "identity": label, "log_odds": odds, "p_value": p}
            )
        except Exception:
            pass

    if not records:
        return

    result = pd.DataFrame(records).sort_values("log_odds")
    print("\n" + "=" * 60)
    print(f"MODEL 4 — Per-domain log-odds vs reference identity ({ref})")
    print("=" * 60)
    print(result.to_string(index=False))

    # Forest plot
    fig, ax = plt.subplots(figsize=(10, max(4, len(result) * 0.4)))
    colors = ["#d7191c" if p < 0.05 else "#aaaaaa" for p in result["p_value"]]
    ax.barh(result["domain"], result["log_odds"], color=colors)
    ax.axvline(0, color="black", linewidth=0.8, linestyle="--")
    ax.set_xlabel(f"Log-odds vs {ref}")
    ax.set_title(
        f"Per-domain targeting differences by proxy identity\n(red = significant at α=0.05)"
    )
    plt.tight_layout()
    path = os.path.join(output_dir, "per_domain_odds.png")
    fig.savefig(path, dpi=150)
    print(f"[plot] saved → {path}")
    plt.close(fig)


# ── Summary stats ─────────────────────────────────────────────────────────────


def summary_stats(df: pd.DataFrame) -> None:
    print("\n" + "=" * 60)
    print("DESCRIPTIVE STATISTICS")
    print("=" * 60)
    print(f"Total observations : {len(df)}")
    print(f"Unique trials      : {df['trial_id'].nunique()}")
    print(f"Proxy identities   : {df['zip_condition'].value_counts().to_dict()}")
    print(f"Unique ad domains  : {df['ad_domain'].nunique()}")
    print(f"Unique ad networks : {df['ad_network'].nunique()}")
    if "source_type" in df:
        print(f"Source types       : {df['source_type'].value_counts().to_dict()}")
    if "intent_profile" in df:
        print(f"Intent profiles    : {df['intent_profile'].value_counts().to_dict()}")


def treatment_cell_summary(df: pd.DataFrame) -> None:
    print("\n" + "=" * 60)
    print("TREATMENT CELLS  (intent profile × proxy identity)")
    print("=" * 60)
    cell = df.groupby(["intent_profile", "zip_condition"]).size().unstack(fill_value=0)
    print(cell.to_string())

    print("\nClean labels by treatment cell (top 12):")
    breakdown = (
        df.groupby(["intent_profile", "zip_condition", "clean_topic_label"])
        .size()
        .reset_index(name="n")
        .sort_values(
            ["intent_profile", "zip_condition", "n"], ascending=[True, True, False]
        )
    )
    for (intent, zip_condition), sub in breakdown.groupby(
        ["intent_profile", "zip_condition"]
    ):
        print(f"\n[{intent} × {zip_condition}]")
        print(sub.head(12)[["clean_topic_label", "n"]].to_string(index=False))


def final_taxonomy_summary(df: pd.DataFrame, output_dir: str) -> None:
    print("\n" + "=" * 60)
    print("FINAL SIMPLIFIED TAXONOMY")
    print("=" * 60)
    print(df["final_taxonomy"].value_counts().to_string())

    by_cell = (
        df.groupby(["intent_profile", "zip_condition", "final_taxonomy"])
        .size()
        .reset_index(name="n")
    )

    print("\nBy ZIP × intent_profile:")
    table = by_cell.pivot_table(
        index=["intent_profile", "zip_condition"],
        columns="final_taxonomy",
        values="n",
        fill_value=0,
    )
    print(table.to_string())

    csv_path = os.path.join(output_dir, "final_taxonomy_summary.csv")
    table.reset_index().to_csv(csv_path, index=False)
    print(f"\n[analysis] Final taxonomy summary saved → {csv_path}")


def cell_comparison_plot(df: pd.DataFrame, output_dir: str) -> None:
    plot_df = (
        df.groupby(["intent_profile", "zip_condition", "final_taxonomy"])
        .size()
        .reset_index(name="n")
    )
    plot_df["cell"] = plot_df["intent_profile"] + " × " + plot_df["zip_condition"]

    pivot = plot_df.pivot_table(
        index="cell",
        columns="final_taxonomy",
        values="n",
        fill_value=0,
    )

    pivot_pct = pivot.div(pivot.sum(axis=1), axis=0) * 100
    # Build dynamic ordering: group by intent profile, then sort by identity label
    ordered_cells = sorted(
        pivot_pct.index.tolist(), key=lambda c: (c.split(" × ")[0], c.split(" × ")[-1])
    )
    pivot_pct = pivot_pct.reindex([c for c in ordered_cells if c in pivot_pct.index])

    fig, ax = plt.subplots(figsize=(12, 6))
    pivot_pct.plot(
        kind="bar",
        stacked=True,
        ax=ax,
        color=["#4daf4a", "#377eb8", "#e41a1c", "#984ea3", "#ff7f00"],
    )
    ax.set_ylabel("Share of observations (%)")
    ax.set_xlabel("Treatment cell (intent profile × proxy identity)")
    ax.set_title("Proxy identity × intent_profile comparison by final taxonomy")
    ax.legend(title="Final taxonomy", bbox_to_anchor=(1.02, 1), loc="upper left")
    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()
    path = os.path.join(output_dir, "cell_comparison_taxonomy.png")
    fig.savefig(path, dpi=150)
    print(f"[plot] saved → {path}")
    plt.close(fig)


def google_search_summary(df: pd.DataFrame, output_dir: str) -> None:
    sub = df[df["source_type"] == "google_search_ad"].copy()
    if sub.empty:
        print("\n[analysis] no Google search ads found in this dataset.")
        return

    print("\n" + "=" * 60)
    print("GOOGLE SEARCH ADS — Topic summary")
    print("=" * 60)
    print(
        sub.groupby(["query_topic", "zip_condition"])
        .size()
        .unstack(fill_value=0)
        .to_string()
    )

    print("\nTop inferred ad topics:")
    print(
        sub["inferred_topic"].replace("", "unknown").value_counts().head(15).to_string()
    )

    print("\nSample Google ads:")
    sample = sub[
        [
            "zip_condition",
            "query_topic",
            "search_query",
            "advertiser_name",
            "ad_headline",
            "landing_domain",
            "inferred_topic",
        ]
    ].head(20)
    print(sample.to_string(index=False))

    csv_path = os.path.join(output_dir, "google_search_ads.csv")
    sub.to_csv(csv_path, index=False)
    print(f"\n[analysis] Google ads saved → {csv_path}")


def inferred_topic_summary(df: pd.DataFrame, output_dir: str) -> None:
    sub = df[df["clean_topic_label"] != ""].copy()
    if sub.empty:
        print("\n[analysis] no inferred ad topics found in this dataset.")
        return

    print("\n" + "=" * 60)
    print("CLEAN AD TYPE LABELS")
    print("=" * 60)
    print(sub["clean_topic_label"].value_counts().head(20).to_string())

    by_zip = (
        sub.groupby(["inferred_topic", "zip_condition"]).size().unstack(fill_value=0)
    )
    print("\nBy ZIP condition:")
    by_zip = (
        sub.groupby(["clean_topic_label", "zip_condition"]).size().unstack(fill_value=0)
    )
    print(by_zip.to_string())

    print("\nPlatform classes:")
    print(sub["platform_class"].value_counts().to_string())

    print("\nRequest roles:")
    print(sub["request_role"].value_counts().head(20).to_string())

    csv_path = os.path.join(output_dir, "classified_ads.csv")
    sub.to_csv(csv_path, index=False)
    print(f"\n[analysis] Classified ads saved → {csv_path}")


# ── Entry point ───────────────────────────────────────────────────────────────


async def main(output_dir: str) -> None:
    os.makedirs(output_dir, exist_ok=True)

    print("[analysis] loading data …")
    df = await load_data()

    if df.empty:
        print("[analysis] no data found. Run experiment.py first.")
        return

    df = preprocess(df)

    summary_stats(df)
    treatment_cell_summary(df)
    google_search_summary(df, output_dir)
    inferred_topic_summary(df, output_dir)
    final_taxonomy_summary(df, output_dir)
    cell_comparison_plot(df, output_dir)
    logistic_regression(df)
    chi_square_test(df)
    domain_breakdown(df, output_dir)
    per_domain_odds(df, output_dir)

    # Save processed dataset
    csv_path = os.path.join(output_dir, "observations.csv")
    df.to_csv(csv_path, index=False)
    print(f"\n[analysis] dataset saved → {csv_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Causal analysis of ad-targeting experiment"
    )
    parser.add_argument(
        "--output", default="results", help="Directory for plots and CSV"
    )
    args = parser.parse_args()

    asyncio.run(main(args.output))
