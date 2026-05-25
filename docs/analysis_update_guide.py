"""
Analysis Update Guide: Using page_load_time_ms as a Covariate
=============================================================

This guide shows how to update your regression models to control for proxy
latency using the new page_load_time_ms field.
"""

import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf

# ── Load data ──────────────────────────────────────────────────────────────
# Assuming you have a function that loads observations from DB
# df = load_observations_from_db()

# For this example, we'll show the analysis patterns

# ── Filter to new data only (optional) ────────────────────────────────────
# If you want to compare before/after warming phase implementation:
# df_new = df[df['page_load_time_ms'].notna()]
# df_old = df[df['page_load_time_ms'].isna()]


# ══════════════════════════════════════════════════════════════════════════
# Model 1: Ad Volume (Poisson GLM) — WITH Latency Control
# ══════════════════════════════════════════════════════════════════════════

def analyze_volume_with_latency_control(df):
    """
    Compare geographic bias before and after controlling for page load time.
    
    Expected outcome:
    - If zip_condition coefficient stays significant → PROVEN GEOGRAPHIC BIAS
    - If zip_condition coefficient becomes non-significant → LATENCY CONFOUND
    """
    
    # Aggregate to trial × zip × intent level
    trial_agg = (
        df.groupby(['trial_id', 'zip_condition', 'intent_profile', 'measurement_site'])
        .agg(
            ad_count=('ad_url', 'count'),
            avg_load_time=('page_load_time_ms', 'mean')
        )
        .reset_index()
    )
    
    print("=" * 70)
    print("Model 1a: Ad Volume WITHOUT Latency Control")
    print("=" * 70)
    model_no_latency = smf.glm(
        'ad_count ~ C(zip_condition) + C(intent_profile) + C(measurement_site)',
        data=trial_agg,
        family=sm.families.Poisson()
    ).fit()
    print(model_no_latency.summary())
    
    print("\n" + "=" * 70)
    print("Model 1b: Ad Volume WITH Latency Control")
    print("=" * 70)
    model_with_latency = smf.glm(
        'ad_count ~ C(zip_condition) + C(intent_profile) + C(measurement_site) + avg_load_time',
        data=trial_agg,
        family=sm.families.Poisson()
    ).fit()
    print(model_with_latency.summary())
    
    # Interpretation guide
    print("\n" + "=" * 70)
    print("INTERPRETATION GUIDE")
    print("=" * 70)
    coef_no_latency = model_no_latency.params.filter(like='zip_condition')
    coef_with_latency = model_with_latency.params.filter(like='zip_condition')
    
    print("\nZip Condition Coefficients:")
    print(f"  Without latency control: {coef_no_latency.values}")
    print(f"  With latency control:    {coef_with_latency.values}")
    
    pval_no_latency = model_no_latency.pvalues.filter(like='zip_condition')
    pval_with_latency = model_with_latency.pvalues.filter(like='zip_condition')
    
    print(f"\nZip Condition P-values:")
    print(f"  Without latency control: {pval_no_latency.values}")
    print(f"  With latency control:    {pval_with_latency.values}")
    
    if (pval_with_latency < 0.05).any():
        print("\n✓ RESULT: Geographic bias persists after controlling for latency")
        print("  → This is evidence of ADVERTISER TARGETING based on location")
    else:
        print("\n✓ RESULT: Geographic bias disappears after controlling for latency")
        print("  → This was a TECHNICAL ARTIFACT (slow proxy causing bid timeouts)")


# ══════════════════════════════════════════════════════════════════════════
# Model 4: Ad Category Distribution (Multinomial Logit) — WITH Latency
# ══════════════════════════════════════════════════════════════════════════

def analyze_category_distribution_with_latency(df):
    """
    Test if intent profile affects ad category distribution, controlling for
    page load time.
    
    Expected outcome with warming phase:
    - High Income → luxury goods, premium services, investment ads
    - Low Income → budget services, personal loans, job ads
    - Latency should NOT significantly affect category distribution
    """
    
    # Filter to observations with inferred topics
    df_topics = df[df['inferred_topic'].notna()].copy()
    
    # Create binary indicators for each major topic
    top_topics = df_topics['inferred_topic'].value_counts().head(10).index
    
    for topic in top_topics:
        print(f"\n{'=' * 70}")
        print(f"Topic: {topic}")
        print('=' * 70)
        
        df_topics[f'is_{topic}'] = (df_topics['inferred_topic'] == topic).astype(int)
        
        # Aggregate to trial level
        trial_topic = (
            df_topics.groupby(['trial_id', 'zip_condition', 'intent_profile'])
            .agg({
                f'is_{topic}': 'sum',
                'page_load_time_ms': 'mean',
                'ad_url': 'count'
            })
            .reset_index()
            .rename(columns={'ad_url': 'total_ads'})
        )
        
        # Logistic regression: P(ad is topic | intent, zip, latency)
        model = smf.logit(
            f'is_{topic} ~ C(intent_profile) + C(zip_condition) + page_load_time_ms',
            data=df_topics
        ).fit()
        
        print(model.summary())
        
        # Check if intent_profile is significant
        intent_pvals = model.pvalues.filter(like='intent_profile')
        if (intent_pvals < 0.05).any():
            print(f"\n✓ Intent profile SIGNIFICANTLY affects '{topic}' exposure")
        else:
            print(f"\n✗ Intent profile does NOT affect '{topic}' exposure")


# ══════════════════════════════════════════════════════════════════════════
# Diagnostic: Latency Distribution by Proxy
# ══════════════════════════════════════════════════════════════════════════

def diagnose_latency_patterns(df):
    """
    Check if certain proxies are systematically slower.
    This validates the need for latency control.
    """
    
    latency_summary = (
        df.groupby('zip_condition')['page_load_time_ms']
        .describe()
    )
    
    print("=" * 70)
    print("Page Load Time Distribution by Proxy")
    print("=" * 70)
    print(latency_summary)
    
    # Statistical test: Are latency distributions different across proxies?
    from scipy.stats import kruskal
    
    groups = [
        df[df['zip_condition'] == cond]['page_load_time_ms'].dropna()
        for cond in df['zip_condition'].unique()
    ]
    
    stat, pval = kruskal(*groups)
    
    print(f"\nKruskal-Wallis Test (non-parametric ANOVA):")
    print(f"  H-statistic: {stat:.2f}")
    print(f"  p-value: {pval:.4f}")
    
    if pval < 0.05:
        print("\n✓ RESULT: Proxies have significantly different latencies")
        print("  → Latency control is ESSENTIAL for valid causal inference")
    else:
        print("\n✗ RESULT: Proxies have similar latencies")
        print("  → Latency control is less critical (but still good practice)")


# ══════════════════════════════════════════════════════════════════════════
# Validation: Warming Phase Effectiveness
# ══════════════════════════════════════════════════════════════════════════

def validate_warming_phase_effectiveness(df_before, df_after):
    """
    Compare topic diversity before and after warming phase implementation.
    
    Expected outcome:
    - BEFORE: High proportion of generic "News & Lifestyle" ads
    - AFTER: More diverse, intent-specific ad categories
    """
    
    def calculate_topic_entropy(df):
        """Shannon entropy of topic distribution (higher = more diverse)"""
        import numpy as np
        topic_counts = df['inferred_topic'].value_counts()
        probs = topic_counts / topic_counts.sum()
        return -np.sum(probs * np.log2(probs))
    
    print("=" * 70)
    print("Warming Phase Effectiveness: Topic Diversity")
    print("=" * 70)
    
    for profile in ['high_income', 'low_income', 'neutral']:
        before_entropy = calculate_topic_entropy(
            df_before[df_before['intent_profile'] == profile]
        )
        after_entropy = calculate_topic_entropy(
            df_after[df_after['intent_profile'] == profile]
        )
        
        print(f"\n{profile.upper()}:")
        print(f"  Topic entropy BEFORE warming: {before_entropy:.2f}")
        print(f"  Topic entropy AFTER warming:  {after_entropy:.2f}")
        print(f"  Change: {'+' if after_entropy > before_entropy else ''}{after_entropy - before_entropy:.2f}")
        
        if after_entropy > before_entropy:
            print("  ✓ Warming phase INCREASED topic diversity (as expected)")
        else:
            print("  ✗ WARNING: Topic diversity decreased (investigate)")


# ══════════════════════════════════════════════════════════════════════════
# EXAMPLE USAGE
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # This is a template - you'll need to load your actual data
    
    # Example 1: Load all observations
    # df = pd.read_sql("SELECT * FROM ad_observations", connection)
    
    # Example 2: Run volume analysis with latency control
    # analyze_volume_with_latency_control(df)
    
    # Example 3: Check latency patterns
    # diagnose_latency_patterns(df)
    
    # Example 4: Compare before/after warming phase
    # df_before = pd.read_csv('results_before_warming.csv')
    # df_after = pd.read_csv('results_after_warming.csv')
    # validate_warming_phase_effectiveness(df_before, df_after)
    
    print("Analysis templates loaded successfully.")
    print("Update the main block with your actual data loading logic.")
