# Stock research at a glance

The Gradio Space and local Streamlit app share one dashboard renderer (`app/dashboard.py`) and stylesheet (`app/dashboard.css`). Existing research, provider, forecasting, audit, authentication/key handling and API contracts are reused. There is no new runtime dependency or LLM call for the dashboard.

## Five sections

1. **Stock Snapshot:** company, ticker, last close and timestamp, market cap, sector/industry, peer-relative research score, and a short deterministic summary. “Overall Outlook” is explicitly the **1-month** forecast; it is not an invented blend of three different horizons.
2. **Outlook:** three independent cards with the engine's direction, confidence, median expected return, lower/median/upper prices, at most four concise reasons, and the first key risk. Full evidence and risks remain expandable. Phones can swipe between the three cards. Outlook starts on the first screen at the tested phone sizes.
3. **Better Alternatives:** at most three visible cards for the selected horizon. The default is 1 month. Native radio controls switch cached results, support keyboard navigation and require no network request. Order and qualification come from the existing forecast engine. A positive relative return difference is labelled in percentage points; it is not a promised return. If none qualify: “No stronger alternative found currently.”
4. **Competitor Comparison:** the selected stock and the first three comparable peers from the existing universe. Only Growth, Profitability, Valuation and Momentum are shown. Original dimension scores are retained; overall ranks use existing composite scores among the displayed companies. Ties share a rank. Leaders are described only when scores differ, so a tied/flat dimension does not create a fictitious unique strength.
5. **Investment Checklist:** peer-relative flags for growth, profitability, balance sheet, momentum and valuation, plus a conservative risk flag. Bank balance sheets use the existing Capital score rather than corporate debt ratios.

**Advanced Analysis** is closed by default. It retains detailed charts, financials, ownership, management, earnings calls, news, the full narrative, source evidence, methodology, notices and HTML/Markdown/JSON downloads. Research settings and optional AI keys are also collapsed; stock search stays at the top on phones.

## Meaning of the compact labels

- The research score reuses the existing composite peer score. Strong means at least 70/100, Weak at most 30/100, otherwise Neutral. These are relative research labels, not buy/sell calls or predicted returns.
- Missing scores say “Not enough data”; missing or invalid forecasts say “Unavailable”. No missing value becomes zero, Weak or Neutral.
- The one-line summary uses only available Growth/Profitability scores and the month forecast. Known engine phrases are translated into plain language without changing numbers or positive/negative meaning. Long headlines remain a single visual line, with their full text in the expandable evidence.
- Confidence stays the engine's **score out of 100**, not a calibrated probability. Expected return is the **median estimate**. The price range is unchanged; display rounding uses the currency's usual two decimals.
- Risk is Unknown without fresh outlook or price-risk data. Observed annual volatility above 40%, drawdown below −35%, or elevated event/liquidity/model-range flags produce High. Incomplete data/event coverage produces at least Medium. Low requires complete data, volatility below 20% and drawdown smaller than 15%. These are disclosed screening rules, not a personal risk assessment.
- Data-quality status and the price timestamp remain visible even when the detailed warnings are collapsed. The educational disclaimer remains visible beneath the five sections.

## Accessibility and deployment

Regular Arial/Helvetica typography, semantic headings, labelled native radio controls, keyboard focus outlines, visible direction/status words, and icons supplement color. Cards adapt to available width, including the embedded Hugging Face frame. Gradio's default prose styles are disabled only for the shared dashboard so they cannot override card sizes or spacing.

The native Gradio launch and disabled SSR proxy remain in place for the existing ZeroGPU deployment. The Space build script copies the shared renderer and stylesheet. UI tests cover both frontends, score boundaries, ties, missing/stale inputs, safe HTML, alternative periods/limits, and unchanged forecast numbers. Browser checks additionally cover mobile overflow, first-screen outlook visibility, keyboard period switching and collapsed details. Test screenshots use local fixtures and are not published as market forecasts.
