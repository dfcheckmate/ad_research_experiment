"""Causal analysis — estimates effect of proxy identity on ad composition."""

from __future__ import annotations

import argparse
import asyncio
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy.stats import chi2_contingency

from config import DB_URL
from logging_config import configure_logging, get_logger, log_result

configure_logging()
logger = get_logger(__name__)

USE_SQLITE = DB_URL.startswith("sqlite")
SQLITE_PATH = DB_URL.removeprefix("sqlite:///") if USE_SQLITE else None

sns.set_theme(style="whitegrid")


async def load_data() -> pd.DataFrame:
    if USE_SQLITE:
        import aiosqlite

        parent = os.path.dirname(SQLITE_PATH)
        if parent:
            os.makedirs(parent, exist_ok=True)

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

            cols = list(rows[0].keys())
            return pd.DataFrame([dict(row) for row in rows], columns=cols)

    import asyncpg

    pool = await asyncpg.create_pool(DB_URL, min_size=2, max_size=5)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT trial_id, zip_condition, ad_url, ad_domain, "
            "ad_network, measurement_site, source_type, intent_profile, query_topic, search_query, "
            "ad_headline, ad_description, advertiser_name, landing_url, landing_domain, "
            "inferred_topic, observed_at "
            "FROM ad_observations ORDER BY observed_at"
        )
    await pool.close()
    return pd.DataFrame([dict(r) for r in rows])


def preprocess(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ad_domain"] = df["ad_domain"].fillna("unknown")
    df["ad_network"] = df["ad_network"].fillna("unknown")
    df["source_type"] = df.get("source_type", pd.Series([None] * len(df))).fillna(
        "page_load"
    )
    df["intent_profile"] = df.get("intent_profile", pd.Series([None] * len(df))).fillna(
        "none"
    )
    df["query_topic"] = df.get("query_topic", pd.Series([None] * len(df))).fillna(
        "none"
    )
    df["search_query"] = df.get("search_query", pd.Series([None] * len(df))).fillna("")
    df["inferred_topic"] = df.get("inferred_topic", pd.Series([None] * len(df))).fillna(
        "uncategorized"
    )

    if "ad_headline" in df.columns:
        df["ad_headline"] = df["ad_headline"].fillna("")
    if "ad_description" in df.columns:
        df["ad_description"] = df["ad_description"].fillna("")

    df["clean_topic_label"] = "uncategorized"
    uncategorized = (
        df["inferred_topic"]
        .str.lower()
        .str.contains("uncategorized|unknown|other", na=False)
    )
    df.loc[uncategorized, "clean_topic_label"] = "uncategorized"

    if "landing_domain" in df.columns:
        df.loc[
            df["landing_domain"].str.contains("google", na=False), "clean_topic_label"
        ] = "google_search"
        df.loc[
            df["landing_domain"].str.contains("youtube", na=False), "clean_topic_label"
        ] = "youtube"

    df["platform_class"] = "other"
    df.loc[df["ad_network"].str.contains("google", na=False), "platform_class"] = (
        "google"
    )
    df.loc[df["ad_network"].str.contains("amazon", na=False), "platform_class"] = (
        "amazon"
    )
    df.loc[df["ad_network"].str.contains("criteo", na=False), "platform_class"] = (
        "criteo"
    )
    df.loc[df["ad_network"].str.contains("taboola", na=False), "platform_class"] = (
        "taboola"
    )
    df.loc[df["ad_network"].str.contains("outbrain", na=False), "platform_class"] = (
        "outbrain"
    )

    df["request_role"] = "other"
    df.loc[df["source_type"] == "google_search_ad", "request_role"] = "search_ad"
    df.loc[df["source_type"].isin(["page_load", "iframe", "xhr"]), "request_role"] = (
        "display_ad"
    )

    df["final_taxonomy"] = df["clean_topic_label"]
    df.loc[df["platform_class"] == "google", "final_taxonomy"] = "google_search"
    df.loc[df["platform_class"] == "amazon", "final_taxonomy"] = "amazon_ads"

    return df


def summary_stats(df: pd.DataFrame) -> None:
    print("\n" + "=" * 60)
    print("DESCRIPTIVE STATISTICS")
    print("=" * 60)
    print(f"Total observations : {len(df)}")
    print(f"Unique trials      : {df['trial_id'].nunique()}")
    print(f"Proxy identities   : {df['zip_condition'].value_counts().to_dict()}")
    print(f"Unique ad domains  : {df['ad_domain'].nunique()}")
    print(f"Unique ad networks : {df['ad_network'].nunique()}")

    if "source_type" in df.columns:
        print(f"Source types       : {df['source_type'].value_counts().to_dict()}")
    if "intent_profile" in df.columns:
        print(f"Intent profiles    : {df['intent_profile'].value_counts().to_dict()}")

    domain_counts = df["ad_domain"].value_counts()
    domain_probs = domain_counts / domain_counts.sum()
    entropy = -(domain_probs * np.log(domain_probs + 1e-10)).sum()
    hhi = (domain_probs**2).sum()

    print(f"Domain entropy (Shannon): {entropy:.4f}")
    print(f"Domain HHI (Herfindahl): {hhi:.4f}")
    print(f"Unique domains per observation: {len(domain_counts) / len(df):.4f}")


def treatment_cell_summary(df: pd.DataFrame) -> None:
    print("\n" + "=" * 60)
    print("TREATMENT CELLS  (intent profile × proxy identity)")
    print("=" * 60)

    if "intent_profile" in df.columns:
        cell = pd.crosstab(df["intent_profile"], df["zip_condition"])
    else:
        cell = pd.crosstab(df["zip_condition"], df["zip_condition"])

    print(cell.to_string())

    if "inferred_topic" in df.columns and "intent_profile" in df.columns:
        print("\nClean labels by treatment cell (top 12):")
        for intent in df["intent_profile"].unique():
            for zip_condition in df["zip_condition"].unique():
                sub = df[
                    (df["intent_profile"] == intent)
                    & (df["zip_condition"] == zip_condition)
                ]
                if len(sub) == 0:
                    continue
                vc = sub["inferred_topic"].value_counts().head(12)
                sub2 = pd.DataFrame({"clean_topic_label": vc.index, "n": vc.values})
                print(f"\n[{intent} × {zip_condition}]")
                print(sub2.head(12)[["clean_topic_label", "n"]].to_string(index=False))


def final_taxonomy_summary(df: pd.DataFrame, output_dir: str) -> None:
    print("\n" + "=" * 60)
    print("FINAL SIMPLIFIED TAXONOMY")
    print("=" * 60)
    print(df["final_taxonomy"].value_counts().to_string())

    if "intent_profile" in df.columns:
        print("\nBy ZIP × intent_profile:")
        table = pd.crosstab(
            [df["zip_condition"], df["intent_profile"]], df["final_taxonomy"]
        )
        print(table.to_string())

    csv_path = os.path.join(output_dir, "final_taxonomy.csv")
    if "intent_profile" in df.columns:
        out_df = pd.crosstab(
            [df["zip_condition"], df["intent_profile"]], df["final_taxonomy"]
        )
    else:
        out_df = pd.crosstab(df["zip_condition"], df["final_taxonomy"])
    out_df.to_csv(csv_path)
    log_result(f"[analysis] Final taxonomy summary saved → {csv_path}")


def cell_comparison_plot(df: pd.DataFrame, output_dir: str) -> None:
    if "intent_profile" not in df.columns:
        return

    pivot = pd.crosstab(
        [df["intent_profile"], df["zip_condition"]], df["platform_class"]
    )
    pivot_pct = pivot.div(pivot.sum(axis=1), axis=0) * 100

    plt.figure(figsize=(10, 6))
    pivot_pct.plot(kind="bar", stacked=True, ax=plt.gca())
    plt.ylabel("Percent of ads")
    plt.title("Platform class mix by intent profile × proxy identity")
    plt.legend(bbox_to_anchor=(1.05, 1), loc="upper left")
    plt.tight_layout()

    path = os.path.join(output_dir, "cell_comparison_platform_class.png")
    plt.savefig(path, dpi=200, bbox_inches="tight")
    plt.close()
    log_result(f"[plot] saved → {path}")


def chi_square_test(df: pd.DataFrame) -> None:
    contingency = pd.crosstab(df["zip_condition"], df["ad_domain"])
    chi2, p, dof, _ = chi2_contingency(contingency)

    print("\n" + "=" * 60)
    print("MODEL 2 — Chi-square: ad domain distribution × ZIP condition")
    print("=" * 60)
    print(f"χ²({dof}) = {chi2:.4f},  p = {p:.6f}")

    if p < 0.05:
        log_result(
            "→ Ad domain distribution differs significantly across ZIP conditions"
        )
    else:
        log_result("→ No significant difference in domain distribution")


def domain_breakdown(df: pd.DataFrame, output_dir: str) -> None:
    pivot = pd.crosstab(df["zip_condition"], df["ad_domain"])
    pivot_pct = pivot.div(pivot.sum(axis=1), axis=0) * 100

    print("\n" + "=" * 60)
    print("MODEL 3 — Ad domain share by proxy identity (%)")
    print("=" * 60)
    print(pivot_pct.round(2).to_string())

    top_domains = pivot.columns[:10]
    pivot_top = pivot[top_domains]

    plt.figure(figsize=(12, 6))
    pivot_top.plot(kind="bar", ax=plt.gca())
    plt.ylabel("Count of ads")
    plt.title("Top ad domains by proxy identity")
    plt.xlabel("Proxy identity (ZIP condition)")
    plt.legend(bbox_to_anchor=(1.05, 1), loc="upper left")
    plt.tight_layout()

    path = os.path.join(output_dir, "domain_breakdown_top10.png")
    plt.savefig(path, dpi=200, bbox_inches="tight")
    plt.close()
    log_result(f"[plot] saved → {path}")


def per_domain_odds(df: pd.DataFrame, output_dir: str) -> None:
    ref = df["zip_condition"].mode().iloc[0]

    results = []
    for domain in df["ad_domain"].unique()[:20]:
        sub = df[df["ad_domain"].isin([domain, ref])]
        sub = sub.copy()
        sub["y"] = (sub["ad_domain"] == domain).astype(int)
        sub["zip_ref"] = sub["zip_condition"].apply(
            lambda x: "other" if x != ref else ref
        )

        try:
            model = smf.logit("y ~ zip_ref", data=sub).fit(disp=0)
            rr = model.params["zip_ref[T.other]"]
            pval = model.pvalues["zip_ref[T.other]"]
            results.append({"domain": domain, "log_odds": rr, "p_value": pval})
        except Exception:
            continue

    if not results:
        return

    result = pd.DataFrame(results)
    result = result.sort_values("p_value")

    print("\n" + "=" * 60)
    print("MODEL 4 — Per-domain log-odds vs reference identity")
    print("=" * 60)
    print(result.to_string(index=False))

    plt.figure(figsize=(10, 6))
    top = result.head(10)
    plt.barh(top["domain"], top["log_odds"])
    plt.xlabel("Log odds ratio")
    plt.title(f"Top domains by identity effect (ref={ref})")
    plt.tight_layout()

    path = os.path.join(output_dir, "per_domain_odds.png")
    plt.savefig(path, dpi=200, bbox_inches="tight")
    plt.close()
    log_result(f"[plot] saved → {path}")


def test_volume_hypothesis(df: pd.DataFrame) -> None:
    ref = df["zip_condition"].mode().iloc[0]

    # Aggregate: count ads per trial
    trial_counts = (
        df.groupby(["trial_id", "zip_condition", "intent_profile"])
        .size()
        .reset_index(name="ad_count")
    )

    print("\n" + "=" * 60)
    print("MODEL 1 — Volume (Balance Check)")
    print(f"          Reference level: {ref}")
    print("=" * 60)

    log_result("[note] Exposure is constant; using Poisson GLM on ad counts per trial.")

    # Fit Poisson GLM: ad_count ~ zip_condition
    model = smf.glm(
        "ad_count ~ zip_condition", data=trial_counts, family=sm.families.Poisson()
    ).fit()

    print(model.summary2())

    print("\nRate ratios vs reference identity:")
    for param in model.params.index:
        if param == "Intercept":
            continue
        coef = model.params[param]
        rr = np.exp(coef)
        pval = model.pvalues[param]
        sig = " *" if pval < 0.05 else ""
        print(f"  {param:55s}  β={coef:+.4f}  RR={rr:.4f}  p={pval:.4f}{sig}")

    if any(model.pvalues.drop("Intercept") < 0.05):
        log_result(
            "→ At least one identity level significantly predicts ad volume (α=0.05)"
        )
    else:
        log_result("→ No significant identity effect detected (α=0.05)")


def google_search_summary(df: pd.DataFrame, output_dir: str) -> None:
    if "source_type" not in df.columns:
        log_result("\n[analysis] no Google search ads found in this dataset.")
        return

    google_ads = df[df["source_type"] == "google_search_ad"].copy()
    if google_ads.empty:
        log_result("\n[analysis] no Google search ads found in this dataset.")
        return

    print("\n" + "=" * 60)
    print("GOOGLE SEARCH ADS — Topic summary")
    print("=" * 60)
    print(
        f"Total Google search ads: {len(google_ads)} "
        f"({len(google_ads) / len(df) * 100:.1f}% of all observations)"
    )

    print("\nTop inferred ad topics:")
    print(
        google_ads["inferred_topic"]
        .value_counts()
        .head(10)
        .to_frame(name="count")
        .to_string()
    )

    print("\nSample Google ads:")
    sample = google_ads.sample(min(10, len(google_ads)), random_state=42)
    cols = ["ad_headline", "ad_description", "landing_domain", "inferred_topic"]
    print(sample[cols].to_string(index=False))

    csv_path = os.path.join(output_dir, "google_search_ads.csv")
    google_ads.to_csv(csv_path, index=False)
    log_result(f"\n[analysis] Google ads saved → {csv_path}")


def inferred_topic_summary(df: pd.DataFrame, output_dir: str) -> None:
    if "inferred_topic" not in df.columns:
        log_result("\n[analysis] no inferred ad topics found in this dataset.")
        return

    sub = df.copy()
    sub = sub[
        ~sub["inferred_topic"]
        .str.lower()
        .str.contains("uncategorized|unknown|other", na=False)
    ]

    if sub.empty:
        log_result("\n[analysis] no inferred ad topics found in this dataset.")
        return

    print("\n" + "=" * 60)
    print("CLEAN AD TYPE LABELS")
    print("=" * 60)
    print(sub["clean_topic_label"].value_counts().head(20).to_string())

    print("\nBy ZIP condition:")
    by_zip = pd.crosstab(df["zip_condition"], df["clean_topic_label"])
    print(by_zip.to_string())

    print("\nPlatform classes:")
    print(sub["platform_class"].value_counts().to_string())

    print("\nRequest roles:")
    print(sub["request_role"].value_counts().head(20).to_string())

    csv_path = os.path.join(output_dir, "classified_ads.csv")
    sub.to_csv(csv_path, index=False)
    log_result(f"\n[analysis] Classified ads saved → {csv_path}")


def personalization_analysis(df: pd.DataFrame, output_dir: str) -> None:
    if "intent_profile" not in df.columns:
        return

    print("\n" + "=" * 60)
    print("PERSONALIZATION — Intent Profile × Proxy Identity Stratification")
    print("=" * 60)
    log_result(
        "Testing whether identity effects vary across intent profiles "
        "(interaction / moderation analysis)"
    )

    cell = pd.crosstab(df["intent_profile"], df["zip_condition"])

    for intent in df["intent_profile"].unique():
        sub = df[df["intent_profile"] == intent]
        if len(sub) < 10:
            log_result(f"[{intent}] insufficient data ({len(sub)} rows)")
            continue

        contingency = pd.crosstab(sub["zip_condition"], sub["ad_domain"])
        try:
            chi2, p, dof, _ = chi2_contingency(contingency)
            sig = " *" if p < 0.05 else ""
            log_result(f"[{intent}] χ²({dof}) = {chi2:.2f}, p = {p:.4f}{sig}")
        except Exception as e:
            log_result(f"[{intent}] chi-square failed: {e}")

    print("\nFull intent × identity cell counts (reproduced for context):")
    print(cell.to_string())

    strat_path = os.path.join(output_dir, "personalization_stratification.csv")
    cell.to_csv(strat_path)
    log_result(f"\n[analysis] Personalization stratification saved → {strat_path}")

    if "ad_domain" in df.columns:
        log_result("\n--- Interaction logistic model (personalization) ---")
        log_result(
            "Fitting: ad_domain ~ zip_condition * intent_profile "
            "(logistic regression with interaction terms)"
        )

        sub = df[
            df["ad_domain"].isin(df["ad_domain"].value_counts().head(5).index)
        ].copy()
        sub = sub.copy()
        sub["y"] = sub["ad_domain"].astype("category").cat.codes

        try:
            model = smf.glm(
                "y ~ zip_condition * intent_profile",
                data=sub,
                family=sm.families.Binomial(),
            ).fit()

            int_df = pd.DataFrame(
                {
                    "param": model.params.index,
                    "coef": model.params.values,
                    "p_value": model.pvalues.values,
                }
            )
            int_df = int_df[int_df["param"].str.contains(":")]

            if not int_df.empty:
                print(int_df.head(10).to_string(index=False))

                int_csv = os.path.join(output_dir, "personalization_interaction.csv")
                int_df.to_csv(int_csv, index=False)
                log_result(f"[analysis] Interaction model results saved → {int_csv}")
            else:
                log_result(
                    "[personalization] No interaction terms found — "
                    "identity effects appear consistent across intent profiles"
                )
        except Exception:
            log_result(
                "[personalization] Interaction model failed — insufficient data or separation"
            )


def ranking_analysis(df: pd.DataFrame, output_dir: str) -> None:
    print("\n" + "=" * 60)
    print("RANKING — Temporal Order + Diversity Analysis")
    print("=" * 60)
    log_result("[note] No ad rank/position captured in ad_observations schema.")
    log_result("       Using observed_at timestamp as proxy for order within trial.")
    log_result(
        "       Quartile-based ordering (Q1/Q2/Q3/Q4) within intent_profile blocks."
    )
    log_result("       Time delta features: seconds between consecutive ads.")

    if "observed_at" not in df.columns or df["observed_at"].isna().all():
        log_result(
            "[ranking] No usable observed_at timestamps — skipping order analysis."
        )
        return

    sub = df.copy()
    sub = sub.dropna(subset=["observed_at"])
    sub["observed_at"] = pd.to_datetime(sub["observed_at"])

    # Compute within-block rank and quartile position
    if "intent_profile" in sub.columns:
        sub["within_block_rank"] = sub.groupby(["trial_id", "intent_profile"])[
            "observed_at"
        ].rank(method="first")
        sub["block_size"] = sub.groupby(["trial_id", "intent_profile"])[
            "within_block_rank"
        ].transform("count")
        sub["quartile_position"] = sub["within_block_rank"] / sub["block_size"]
        sub["order_quartile"] = pd.qcut(
            sub["quartile_position"],
            q=4,
            labels=["Q1", "Q2", "Q3", "Q4"],
            duplicates="drop",
        )
    else:
        sub["within_trial_rank"] = sub.groupby("trial_id")["observed_at"].rank(
            method="first"
        )
        trial_size = sub.groupby("trial_id")["within_trial_rank"].transform("count")
        sub["quartile_position"] = sub["within_trial_rank"] / trial_size
        sub["order_quartile"] = pd.qcut(
            sub["quartile_position"],
            q=4,
            labels=["Q1", "Q2", "Q3", "Q4"],
            duplicates="drop",
        )

    # Time delta: seconds since previous ad in same block
    if "intent_profile" in sub.columns:
        sub["prev_observed_at"] = sub.groupby(["trial_id", "intent_profile"])[
            "observed_at"
        ].shift(1)
    else:
        sub["prev_observed_at"] = sub.groupby("trial_id")["observed_at"].shift(1)
    sub["time_delta_sec"] = (
        sub["observed_at"] - sub["prev_observed_at"]
    ).dt.total_seconds()

    # Position estimation stub (for future explicit rank capture)
    sub["estimated_position"] = (
        sub["within_block_rank"]
        if "intent_profile" in sub.columns
        else sub["within_trial_rank"]
    )

    # Diversity metrics per quartile (Shannon entropy of ad domains)
    log_result("\n--- Diversity by quartile position ---")
    entropy_by_quartile = []
    for q in ["Q1", "Q2", "Q3", "Q4"]:
        q_data = sub[sub["order_quartile"] == q]
        if len(q_data) < 5:
            continue
        domain_counts = q_data["ad_domain"].value_counts()
        probs = domain_counts / domain_counts.sum()
        entropy = -np.sum(probs * np.log(probs + 1e-10))
        entropy_by_quartile.append(
            {
                "quartile": q,
                "n_ads": len(q_data),
                "unique_domains": len(domain_counts),
                "shannon_entropy": entropy,
            }
        )

    if entropy_by_quartile:
        entropy_df = pd.DataFrame(entropy_by_quartile)
        print("\nQuartile | N Ads | Unique Domains | Shannon Entropy")
        print("-" * 55)
        for _, row in entropy_df.iterrows():
            print(
                f"{row['quartile']:8s} | {row['n_ads']:5d} | {row['unique_domains']:14d} | {row['shannon_entropy']:.4f}"
            )

        entropy_csv = os.path.join(output_dir, "ranking_diversity_by_quartile.csv")
        entropy_df.to_csv(entropy_csv, index=False)
        log_result(f"[analysis] Diversity metrics saved → {entropy_csv}")

    # Time delta summary
    valid_deltas = sub["time_delta_sec"].dropna()
    if len(valid_deltas) > 0:
        log_result(
            f"\n[ranking] Time delta summary: mean={valid_deltas.mean():.2f}s, median={valid_deltas.median():.2f}s, std={valid_deltas.std():.2f}s"
        )

    # Contingency: quartile × identity
    contingency_quartile = pd.crosstab(sub["order_quartile"], sub["zip_condition"])
    print("\nQuartile × Proxy Identity contingency:")
    print(contingency_quartile.to_string())

    # Chi-square test for quartile distribution across identities
    if contingency_quartile.shape[0] >= 2 and contingency_quartile.shape[1] >= 2:
        try:
            chi2, p, dof, _ = chi2_contingency(contingency_quartile)
            sig = " *" if p < 0.05 else ""
            log_result(f"\n[ranking] χ²({dof}) = {chi2:.2f}, p = {p:.4f}{sig}")
            if p < 0.05:
                log_result(
                    "→ Quartile distribution differs significantly across proxy identities"
                )
            else:
                log_result(
                    "→ No significant difference in quartile distribution across identities"
                )
        except Exception as e:
            log_result(f"[ranking] Chi-square test failed: {e}")

    # Export full ranking data
    rank_csv = os.path.join(output_dir, "ranking_order_proxy.csv")
    export_cols = [
        "trial_id",
        "zip_condition",
        "order_quartile",
        "estimated_position",
        "time_delta_sec",
        "quartile_position",
    ]
    if "intent_profile" in sub.columns:
        export_cols.insert(2, "intent_profile")
    export_cols = [c for c in export_cols if c in sub.columns]
    sub[export_cols].to_csv(rank_csv, index=False)
    log_result(f"[analysis] Enhanced ranking data saved → {rank_csv}")

    # Position estimation stub note
    log_result(
        "\n[note] estimated_position uses temporal rank; update when explicit ad rank capture is added."
    )


async def main(output_dir: str, hypothesis: str = "all") -> None:
    os.makedirs(output_dir, exist_ok=True)

    logger.info("loading data (hypothesis=%s)", hypothesis)
    df = await load_data()

    if df.empty:
        logger.warning("no data found — run experiment.py first")
        return

    df = preprocess(df)

    summary_stats(df)
    treatment_cell_summary(df)

    if hypothesis in ("all", "composition"):
        google_search_summary(df, output_dir)
        inferred_topic_summary(df, output_dir)
        final_taxonomy_summary(df, output_dir)
        cell_comparison_plot(df, output_dir)
        chi_square_test(df)
        domain_breakdown(df, output_dir)
        per_domain_odds(df, output_dir)

    if hypothesis in ("all", "volume"):
        test_volume_hypothesis(df)

    if hypothesis in ("all", "personalization"):
        personalization_analysis(df, output_dir)

    if hypothesis in ("all", "ranking"):
        ranking_analysis(df, output_dir)

    csv_path = os.path.join(output_dir, "observations.csv")
    df.to_csv(csv_path, index=False)
    log_result(f"\n[analysis] dataset saved → {csv_path}")
    log_result(f"\n[analysis] hypothesis={hypothesis} run complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Causal analysis of ad-targeting experiment"
    )
    parser.add_argument(
        "--output", default="out/results", help="Directory for plots and CSV"
    )
    parser.add_argument(
        "--hypothesis",
        default="all",
        choices=["composition", "volume", "ranking", "personalization", "all"],
        help="Analysis type to run",
    )
    args = parser.parse_args()

    asyncio.run(main(args.output, args.hypothesis))
