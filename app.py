from __future__ import annotations

import logging
from datetime import datetime
import re
from typing import Any

import pandas as pd
import streamlit as st

from opportunity_radar.config import get_settings
from opportunity_radar.db import get_database
from opportunity_radar.notifications import (
    build_telegram_watchlist_message,
    build_watchlist_alert_rows,
    send_telegram_message,
)
from opportunity_radar.openrouter import OpenRouterClient
from opportunity_radar.pipeline import run_daily_pipeline

logger = logging.getLogger(__name__)


def _fetch_live_price(symbol: str) -> dict[str, Any] | None:
    """Fetch live stock price from yfinance with .NS suffix for NSE stocks."""
    try:
        import yfinance as yf
        ticker_symbol = symbol.strip().upper()
        if not ticker_symbol.endswith(".NS") and not ticker_symbol.endswith(".BO"):
            ticker_symbol = f"{ticker_symbol}.NS"
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.fast_info
        price = float(getattr(info, 'last_price', 0) or 0)
        prev_close = float(getattr(info, 'previous_close', 0) or 0)
        if price <= 0:
            return None
        change = price - prev_close if prev_close > 0 else 0.0
        change_pct = (change / prev_close * 100) if prev_close > 0 else 0.0
        return {"price": round(price, 2), "change": round(change, 2), "change_pct": round(change_pct, 2)}
    except Exception:
        return None


@st.cache_data(ttl=300)
def get_live_price(symbol: str) -> dict[str, Any] | None:
    """Cached wrapper — 5 min TTL."""
    return _fetch_live_price(symbol)


st.set_page_config(page_title="Opportunity Radar", page_icon="OR", layout="wide")


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700;800&family=IBM+Plex+Sans:wght@400;500;600&display=swap');

        html, body, [class*="css"] {
            font-family: 'IBM Plex Sans', sans-serif;
            background: radial-gradient(ellipse at 18% 0%, #0f1f3d 0%, #060d1b 40%, #030810 80%) !important;
            color: #f0f4fa !important;
        }
        h1, h2, h3 { font-family: 'Sora', sans-serif !important; letter-spacing: 0.3px; color: #ffffff !important; }
        h4, h5, h6 { color: #e8eef8 !important; }
        p, span, div, label { color: #e8eef8; }

        /* --- READABILITY: Force bright text everywhere --- */
        .stMarkdown, .stText, .stCaption, .stAlert p, .element-container { color: #e8eef8 !important; }
        .stCaption, [data-testid="stCaptionContainer"] { color: #b8ccdf !important; opacity: 1 !important; }
        .stTextInput label, .stSelectbox label, .stMultiSelect label, .stTextArea label,
        .stRadio label, .stCheckbox label, .stNumberInput label, .stSlider label {
            color: #e0e8f4 !important; font-weight: 500 !important;
        }
        .stTextInput input, .stTextArea textarea, .stNumberInput input {
            color: #ffffff !important; background: rgba(15,25,50,0.8) !important;
            border-color: rgba(100,160,255,0.25) !important;
        }
        .stTextInput input::placeholder, .stTextArea textarea::placeholder {
            color: #8899bb !important;
        }
        .stSelectbox [data-baseweb="select"], .stMultiSelect [data-baseweb="select"] {
            color: #ffffff !important; background: rgba(15,25,50,0.8) !important;
        }
        [data-testid="stMetricValue"] { color: #ffffff !important; font-weight: 700 !important; }
        [data-testid="stMetricLabel"] { color: #c4d8ef !important; }
        [data-testid="stMetricDelta"] { font-weight: 600 !important; }
        .stDataFrame, .stDataFrame td, .stDataFrame th { color: #e8eef8 !important; }
        .stInfo, .stSuccess, .stWarning, .stError { color: #f0f4fa !important; }
        .stInfo p, .stSuccess p, .stWarning p, .stError p { color: #f0f4fa !important; }
        .stButton button { font-weight: 600 !important; }
        .stExpander summary span { color: #e0e8f4 !important; }
        [data-testid="stForm"] { border-color: rgba(100,160,255,0.2) !important; }
        .stRadio > div > label { color: #e0e8f4 !important; }
        .stToggle label span { color: #e0e8f4 !important; }

        /* --- Glassmorphism Panels --- */
        .or-hero {
            padding: 1.2rem 1.5rem;
            border: 1px solid rgba(100, 160, 255, 0.25);
            border-radius: 16px;
            background: linear-gradient(135deg, rgba(15,35,70,0.92), rgba(8,20,48,0.85));
            backdrop-filter: blur(12px);
            margin-bottom: 1rem;
            box-shadow: 0 0 40px rgba(60,130,246,0.12), inset 0 1px 0 rgba(255,255,255,0.08);
            position: relative;
            overflow: hidden;
        }
        .or-hero::before {
            content: '';
            position: absolute; top: -50%; left: -50%; width: 200%; height: 200%;
            background: conic-gradient(from 0deg, transparent, rgba(99,149,255,0.06), transparent 30%);
            animation: or-rotate 12s linear infinite;
        }
        @keyframes or-rotate { to { transform: rotate(360deg); } }

        .or-glass {
            border: 1px solid rgba(120,160,220,0.22);
            border-radius: 14px;
            padding: 1rem 1.1rem;
            background: rgba(10,22,44,0.7);
            backdrop-filter: blur(8px);
            margin-bottom: 0.8rem;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3);
            transition: border-color 0.3s, box-shadow 0.3s;
            color: #e8eef8;
        }
        .or-glass:hover {
            border-color: rgba(100,160,255,0.4);
            box-shadow: 0 4px 24px rgba(60,130,246,0.18);
        }

        .or-help {
            border: 1px solid rgba(120, 150, 200, 0.22);
            border-radius: 12px;
            padding: 0.9rem 1rem;
            background: rgba(8,20,40,0.7);
            backdrop-filter: blur(6px);
            margin-bottom: 0.9rem;
            color: #e0e8f4;
        }

        /* --- Chips --- */
        .or-chip {
            display: inline-block; font-size: 0.78rem; font-weight: 700;
            padding: 0.22rem 0.6rem; border-radius: 999px;
            border: 1px solid rgba(255,255,255,0.18);
            background: rgba(255,255,255,0.08);
            letter-spacing: 0.3px;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .or-chip:hover { transform: translateY(-1px); }

        /* --- Score Bar --- */
        .or-score-bar {
            height: 7px; border-radius: 4px; margin-top: 0.3rem;
            background: rgba(255,255,255,0.12);
            overflow: hidden;
        }
        .or-score-fill {
            height: 100%; border-radius: 4px;
            transition: width 0.6s ease;
        }

        /* --- Pulse Dot --- */
        .or-pulse {
            display: inline-block; width: 10px; height: 10px;
            border-radius: 50%; background: #4ade80;
            box-shadow: 0 0 8px #4ade80, 0 0 20px rgba(74,222,128,0.3);
            animation: or-pulse-anim 2s ease-in-out infinite;
            margin-right: 8px; vertical-align: middle;
        }
        @keyframes or-pulse-anim {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.5; transform: scale(0.75); }
        }

        /* --- KPI Card --- */
        .or-kpi {
            text-align: center; padding: 0.9rem 0.6rem;
            border: 1px solid rgba(120,160,220,0.18);
            border-radius: 12px;
            background: rgba(12,24,48,0.65);
            backdrop-filter: blur(6px);
        }
        .or-kpi-value { font-size: 1.7rem; font-weight: 700; font-family: 'Sora', sans-serif; color: #ffffff !important; }
        .or-kpi-label { font-size: 0.82rem; color: #c4d8ef !important; margin-top: 0.15rem; font-weight: 500; }

        /* --- Pick Card --- */
        .or-pick {
            border: 1px solid rgba(130,170,220,0.22);
            border-radius: 12px; padding: 0.8rem 0.9rem;
            background: linear-gradient(145deg, rgba(11,23,44,0.85), rgba(8,18,38,0.75));
            backdrop-filter: blur(6px);
            transition: transform 0.25s, border-color 0.3s, box-shadow 0.3s;
            position: relative; overflow: hidden;
            color: #e8eef8;
        }
        .or-pick:hover {
            transform: translateY(-3px);
            border-color: rgba(100,160,255,0.45);
            box-shadow: 0 8px 32px rgba(60,130,246,0.18);
        }

        /* --- Chat Bubble --- */
        .or-chat-user {
            background: rgba(60,130,246,0.2); border: 1px solid rgba(60,130,246,0.35);
            border-radius: 12px 12px 4px 12px; padding: 0.7rem 0.9rem;
            margin: 0.4rem 0; max-width: 85%; margin-left: auto;
            color: #f0f4fa;
        }
        .or-chat-ai {
            background: rgba(10,22,44,0.8); border: 1px solid rgba(120,160,220,0.22);
            border-radius: 12px 12px 12px 4px; padding: 0.7rem 0.9rem;
            margin: 0.4rem 0; max-width: 85%;
            color: #e8eef8;
        }

        /* --- Timeline --- */
        .or-timeline { display: flex; gap: 0.5rem; overflow-x: auto; padding: 0.5rem 0; }
        .or-tl-item {
            min-width: 140px; padding: 0.5rem 0.6rem;
            border: 1px solid rgba(120,160,220,0.18);
            border-radius: 8px; background: rgba(10,22,44,0.6);
            font-size: 0.82rem; flex-shrink: 0;
            color: #e0e8f4;
        }

        /* --- Custom Scrollbar --- */
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: rgba(10,20,40,0.3); }
        ::-webkit-scrollbar-thumb { background: rgba(100,160,255,0.25); border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: rgba(100,160,255,0.4); }

        /* --- Dataframe styling --- */
        .stDataFrame { border-radius: 10px; overflow: hidden; }

        /* --- Sidebar --- */
        section[data-testid="stSidebar"] {
            background: rgba(6,12,24,0.95) !important;
            border-right: 1px solid rgba(100,160,255,0.12) !important;
        }
        section[data-testid="stSidebar"] * { color: #d8e4f2 !important; }
        section[data-testid="stSidebar"] .stCaption { color: #a8bcd4 !important; }

        /* --- Login Page --- */
        .or-login-container {
            max-width: 460px; margin: 4rem auto; padding: 2.5rem;
            border: 1px solid rgba(100,160,255,0.25);
            border-radius: 20px;
            background: linear-gradient(145deg, rgba(12,28,56,0.92), rgba(6,16,36,0.88));
            backdrop-filter: blur(16px);
            box-shadow: 0 0 60px rgba(60,130,246,0.1), 0 20px 60px rgba(0,0,0,0.3);
            text-align: center;
        }
        .or-login-title {
            font-family: 'Sora', sans-serif;
            font-size: 2.4rem; font-weight: 800;
            background: linear-gradient(135deg, #60a5fa, #a78bfa, #60a5fa);
            background-size: 200% auto;
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
            animation: or-gradient-text 3s ease-in-out infinite;
            margin-bottom: 0.3rem;
        }
        @keyframes or-gradient-text {
            0%, 100% { background-position: 0% center; }
            50% { background-position: 100% center; }
        }
        .or-login-subtitle { color: #b8ccdf; font-size: 1rem; margin-bottom: 1.5rem; }

        /* --- Price Tag --- */
        .or-price-up { color: #4ade80 !important; font-weight: 600; }
        .or-price-down { color: #f87171 !important; font-weight: 600; }
        .or-price-neutral { color: #94a3b8 !important; font-weight: 600; }

        /* --- Market Pulse --- */
        .or-market-pulse {
            border: 1px solid rgba(100,160,255,0.18);
            border-radius: 10px; padding: 0.7rem;
            background: rgba(8,18,38,0.7);
            margin-bottom: 0.6rem;
            font-size: 0.85rem;
            color: #d8e4f2;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_resource
def get_db() -> Any:
    return get_database(get_settings())


def _safe_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _zone(score: float) -> str:
    if score >= 60:
        return "Opportunity"
    if score <= 40:
        return "Risk"
    return "Neutral"


def _recommendation(score: float) -> str:
    if score >= 70:
        return "Strong Buy"
    if score >= 60:
        return "Buy"
    if score <= 30:
        return "Strong Risk"
    if score <= 45:
        return "Risk"
    return "Watch"


def _recommendation_chip(label: str) -> str:
    if label in {"Strong Buy", "Buy"}:
        return (
            "<span class='or-chip' style='color:#b9f6ca;"
            "border-color:rgba(116,232,166,0.45)'>"
            f"{label}</span>"
        )
    if label in {"Strong Risk", "Risk"}:
        return (
            "<span class='or-chip' style='color:#ffc7c7;"
            "border-color:rgba(255,145,145,0.45)'>"
            f"{label}</span>"
        )
    return (
        "<span class='or-chip' style='color:#ffe7b8;"
        "border-color:rgba(255,223,145,0.4)'>"
        f"{label}</span>"
    )


def _confidence_band(confidence_multiplier: float, action_score: float) -> str:
    if confidence_multiplier >= 1.15 and action_score >= 8:
        return "High"
    if confidence_multiplier >= 1.0 and action_score >= 4:
        return "Medium"
    return "Low"


def _confidence_meter_value(label: str) -> float:
    if label == "High":
        return 0.9
    if label == "Medium":
        return 0.6
    return 0.3


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_user_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_\-]", "", str(value or "").strip().lower())
    return normalized or "investor"


def _build_watchlist_alerts(watchlist_df: pd.DataFrame) -> list[dict[str, Any]]:
    if watchlist_df.empty:
        return []

    alerts: list[dict[str, Any]] = []
    ranked = watchlist_df.sort_values("action_score", ascending=False)
    for row in ranked.itertuples():
        score = float(getattr(row, "final_score", 50.0))
        symbol = str(getattr(row, "stock_symbol", ""))
        recommendation = str(getattr(row, "recommendation", "Watch"))
        confidence = str(getattr(row, "confidence_band", "Low"))
        explanation = _short(str(getattr(row, "explanation", "")), 160)

        if score >= 60:
            alert_type = "WATCHLIST_OPPORTUNITY"
        elif score <= 40:
            alert_type = "WATCHLIST_RISK"
        else:
            alert_type = "WATCHLIST_WATCH"

        alerts.append(
            {
                "stock_symbol": symbol,
                "score": round(score, 1),
                "recommendation": recommendation,
                "confidence": confidence,
                "alert_type": alert_type,
                "message": f"{symbol}: {recommendation} ({score:.1f}, confidence {confidence}). {explanation}",
            }
        )
    return alerts


def _build_telegram_digest(
    user_name: str,
    score_df: pd.DataFrame,
    latest_alerts: list[dict[str, Any]],
    watchlist_alerts: list[dict[str, Any]],
) -> str:
    ts = datetime.now().strftime("%d-%b-%Y %H:%M")
    lines = [
        f"Opportunity Radar | Daily Digest | {ts}",
        f"Investor: {user_name}",
        "",
    ]

    if score_df.empty:
        lines.append("No score data available yet. Run a fresh scan.")
        return "\n".join(lines)

    opp = score_df[score_df["final_score"] > 50].sort_values("final_score", ascending=False).head(3)
    risk = score_df[score_df["final_score"] < 50].sort_values("final_score", ascending=True).head(3)

    lines.append("Top Opportunities:")
    if opp.empty:
        lines.append("- None in this run")
    else:
        for row in opp.itertuples():
            lines.append(
                f"- {row.stock_symbol}: {row.final_score:.1f} | {row.recommendation} | {_short(getattr(row, 'explanation', ''), 95)}"
            )

    lines.append("")
    lines.append("Top Risks:")
    if risk.empty:
        lines.append("- None in this run")
    else:
        for row in risk.itertuples():
            lines.append(
                f"- {row.stock_symbol}: {row.final_score:.1f} | {row.recommendation} | {_short(getattr(row, 'explanation', ''), 95)}"
            )

    lines.append("")
    lines.append("Your Watchlist Alerts:")
    if not watchlist_alerts:
        lines.append("- No watchlist symbols saved yet or no watchlist alerts.")
    else:
        for alert in watchlist_alerts[:8]:
            lines.append(
                f"- {alert.get('stock_symbol', '')}: {alert.get('recommendation', '')} "
                f"({alert.get('score', 0):.1f}, confidence {alert.get('confidence', 'Low')})"
            )

    lines.append("")
    lines.append("System Alerts:")
    if not latest_alerts:
        lines.append("- No system alerts in latest run")
    else:
        for alert in latest_alerts[:5]:
            lines.append(
                f"- {alert.get('stock_symbol', '')} [{alert.get('alert_type', 'ALERT')}]: {_short(str(alert.get('message', '')), 110)}"
            )

    lines.append("")
    lines.append("Auto-generated by Opportunity Radar")
    return "\n".join(lines)


TERM_EXPLANATIONS: dict[str, str] = {
    "Final Score": "Overall stock score from 0 to 100. Around 50 is neutral, higher means stronger upside signals, lower means higher downside risk.",
    "Recommendation": "Action label generated from score: Strong Buy, Buy, Watch, Risk, or Strong Risk.",
    "Confidence": "How reliable the score is based on signal quality and alignment. High means stronger evidence.",
    "Recency": "How fresh the signals are. Recent signals carry more weight than old signals.",
    "Signal": "A structured event interpreted as actionable intelligence (example: insider buy, earnings beat, regulatory trigger).",
    "Opportunity": "A stock with positive signal bias and score usually above 60.",
    "Risk": "A stock with negative signal bias and score usually below 40.",
    "Watchlist Alert": "Personalized alert generated only for symbols in your personal login watchlist.",
    "Digest": "A compact daily summary designed to be directly sent via Telegram.",
}


QUICK_QUESTIONS: list[str] = [
    "Which stocks look strongest today and why?",
    "Which stocks are highest risk today?",
    "Give me top 3 opportunities with confidence.",
    "Summarize today's alerts in simple language.",
]


def _prepare_scores(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = _safe_df(rows)
    if df.empty:
        return df

    df["final_score"] = pd.to_numeric(df.get("final_score"), errors="coerce").fillna(50.0)
    df["raw_score"] = pd.to_numeric(df.get("raw_score"), errors="coerce").fillna(50.0)
    df["confidence_multiplier"] = pd.to_numeric(df.get("confidence_multiplier"), errors="coerce").fillna(1.0)
    df["recency_multiplier"] = pd.to_numeric(df.get("recency_multiplier"), errors="coerce").fillna(1.0)
    df["rank"] = pd.to_numeric(df.get("rank"), errors="coerce").fillna(0).astype(int)
    df["zone"] = df["final_score"].apply(_zone)
    df["recommendation"] = df["final_score"].apply(_recommendation)
    df["action_score"] = (df["final_score"] - 50.0).abs()
    df["confidence_band"] = df.apply(
        lambda row: _confidence_band(float(row.get("confidence_multiplier", 1.0)), float(row.get("action_score", 0.0))),
        axis=1,
    )
    return df


def _short(text: str, n: int = 140) -> str:
    clean = str(text or "").strip()
    if len(clean) <= n:
        return clean
    return clean[: n - 1].rstrip() + "..."


def _score_bar_color(score: float) -> str:
    if score >= 65:
        return "linear-gradient(90deg, #22c55e, #4ade80)"
    if score >= 55:
        return "linear-gradient(90deg, #3b82f6, #60a5fa)"
    if score <= 35:
        return "linear-gradient(90deg, #ef4444, #f87171)"
    if score <= 45:
        return "linear-gradient(90deg, #f59e0b, #fbbf24)"
    return "linear-gradient(90deg, #64748b, #94a3b8)"


def _render_pick_card(row: pd.Series, idx: int) -> None:
    score = float(row.get("final_score", 50.0))
    reco = str(row.get("recommendation", "Watch"))
    symbol = row.get("stock_symbol", "")
    expl = _short(str(row.get("explanation", "")), 100)
    bar_color = _score_bar_color(score)

    # Live price
    price_html = ""
    live = get_live_price(str(symbol))
    if live:
        pct = live["change_pct"]
        arrow = "▲" if pct >= 0 else "▼"
        css_cls = "or-price-up" if pct >= 0 else "or-price-down"
        price_html = (
            f"<div style='margin-top:0.3rem;font-size:0.85rem'>"
            f"₹{live['price']:,.2f} "
            f"<span class='{css_cls}'>{arrow} {abs(pct):.2f}%</span>"
            f"</div>"
        )

    st.markdown(
        (
            f"<div class='or-pick'>"
            f"<div style='font-weight:700;font-size:1.05rem;color:#ffffff'>#{idx} {symbol}</div>"
            f"{price_html}"
            f"<div style='margin-top:0.3rem'>{_recommendation_chip(reco)}</div>"
            f"<div style='margin-top:0.35rem;font-size:0.95rem;font-weight:600;color:#e8eef8'>{score:.1f}</div>"
            f"<div class='or-score-bar'><div class='or-score-fill' style='width:{score}%;background:{bar_color}'></div></div>"
            f"<div style='margin-top:0.35rem;font-size:0.82rem;color:#d4e4f5'>{expl}</div>"
            f"</div>"
        ),
        unsafe_allow_html=True,
    )


def _fact_pack_for_ai(df: pd.DataFrame, alerts: list[dict[str, Any]]) -> list[str]:
    if df.empty:
        return ["No scores found for latest run."]

    facts = [f"Universe size: {len(df)} stocks"]

    opp = df[df["final_score"] > 50].sort_values("final_score", ascending=False).head(5)
    risk = df[df["final_score"] < 50].sort_values("final_score", ascending=True).head(5)

    if opp.empty:
        facts.append("No high-conviction opportunities in latest run.")
    else:
        for row in opp.itertuples():
            facts.append(
                f"Opportunity: {row.stock_symbol} score {row.final_score:.1f}; explanation: {_short(getattr(row, 'explanation', ''), 120)}"
            )

    if risk.empty:
        facts.append("No major risks in latest run.")
    else:
        for row in risk.itertuples():
            facts.append(
                f"Risk: {row.stock_symbol} score {row.final_score:.1f}; explanation: {_short(getattr(row, 'explanation', ''), 120)}"
            )

    if alerts:
        for alert in alerts[:8]:
            facts.append(
                f"Alert [{alert.get('alert_type','ALERT')}]: {alert.get('stock_symbol','')} - {_short(str(alert.get('message','')), 140)}"
            )
    else:
        facts.append("No alerts generated in latest run.")

    return facts


def _deterministic_query_answer(
    question: str,
    df: pd.DataFrame,
    alerts: list[dict[str, Any]],
) -> str:
    if df.empty:
        return "No score data is available yet. Please run a fresh scan first."

    upper_q = question.upper()
    symbols = set(df["stock_symbol"].astype(str).str.upper().tolist())
    mentioned = [sym for sym in symbols if re.search(rf"\b{re.escape(sym)}\b", upper_q)]

    if mentioned:
        sym = mentioned[0]
        row = df[df["stock_symbol"].astype(str).str.upper() == sym].iloc[0]
        return (
            f"{sym} currently has score {float(row['final_score']):.1f} ({row['recommendation']}). "
            f"Reason: {row.get('explanation', 'No explanation available.')}"
        )

    top_opp = df[df["final_score"] > 50].sort_values("final_score", ascending=False).head(3)
    top_risk = df[df["final_score"] < 50].sort_values("final_score", ascending=True).head(3)

    lines = ["Here is the latest market signal snapshot from your radar:"]

    if top_opp.empty:
        lines.append("- No strong opportunity stocks were identified in this run.")
    else:
        lines.append(
            "- Top opportunities: "
            + ", ".join(f"{row.stock_symbol} ({row.final_score:.1f})" for row in top_opp.itertuples())
        )

    if top_risk.empty:
        lines.append("- No major downside-risk stocks were identified in this run.")
    else:
        lines.append(
            "- Top risks: "
            + ", ".join(f"{row.stock_symbol} ({row.final_score:.1f})" for row in top_risk.itertuples())
        )

    if alerts:
        lines.append(
            "- Latest alert: "
            + _short(str(alerts[0].get("message", "")), 150)
        )

    lines.append("Ask about a specific stock symbol (example: HCLTECH) for detailed reasoning.")
    return "\n".join(lines)


def render_dashboard(db: Any, settings: Any, beginner_mode: bool) -> None:
    st.subheader("Dashboard")
    st.markdown(
        """
        <div class='or-help'>
        <b>Simple workflow</b><br/>
        1. Click <b>Run Fresh Scan</b>.<br/>
        2. See <b>Today's 5 Picks</b> and <b>Top Risks</b>.<br/>
        3. Open <b>Explain Stock</b> for detailed reasoning.
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns([1.1, 2.2])
    with c1:
        if st.button("Run Fresh Scan", type="primary", use_container_width=True):
            with st.spinner("Running scan..."):
                summary = run_daily_pipeline(settings=settings, db=db)
            st.session_state["last_scan_summary"] = summary
            st.success(
                f"Done in {summary.get('runtime_seconds', 0)}s | "
                f"stocks={summary.get('stock_count', 0)} | signals={summary.get('signal_count', 0)}"
            )
    with c2:
        summary = st.session_state.get("last_scan_summary")
        if summary:
            st.info(
                f"Last scan: {summary.get('run_date', '')} | "
                f"mode={summary.get('mode', '')} | "
                f"alerts={summary.get('alert_count', 0)}"
            )
        else:
            st.caption("No in-session scan result yet. Showing latest stored run.")

    rows = db.fetch_latest_scores(limit=max(settings.max_stock_universe, 120))
    df = _prepare_scores(rows)
    if df.empty:
        st.info("No scored stocks found yet. Run Fresh Scan once.")
        return

    search = st.text_input("Search stock symbol", value="").strip().upper()
    if search:
        df = df[df["stock_symbol"].astype(str).str.upper().str.contains(search)]

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.markdown(
            f"<div class='or-kpi'><div class='or-kpi-value'>{len(df):,}</div>"
            f"<div class='or-kpi-label'>📊 Stocks Scanned</div></div>",
            unsafe_allow_html=True,
        )
    with k2:
        opp_count = int((df["zone"] == "Opportunity").sum())
        st.markdown(
            f"<div class='or-kpi'><div class='or-kpi-value' style='color:#4ade80'>{opp_count}</div>"
            f"<div class='or-kpi-label'>🟢 Opportunities</div></div>",
            unsafe_allow_html=True,
        )
    with k3:
        risk_count = int((df["zone"] == "Risk").sum())
        st.markdown(
            f"<div class='or-kpi'><div class='or-kpi-value' style='color:#f87171'>{risk_count}</div>"
            f"<div class='or-kpi-label'>🔴 Risks</div></div>",
            unsafe_allow_html=True,
        )
    with k4:
        neutral_count = int((df["zone"] == "Neutral").sum())
        st.markdown(
            f"<div class='or-kpi'><div class='or-kpi-value' style='color:#fbbf24'>{neutral_count}</div>"
            f"<div class='or-kpi-label'>🟡 Neutral</div></div>",
            unsafe_allow_html=True,
        )

    st.markdown("### Today's 5 Picks")
    picks = df.sort_values(["action_score", "rank"], ascending=[False, True]).head(5)
    cols = st.columns(5)
    for idx, (_, row) in enumerate(picks.iterrows(), start=1):
        with cols[(idx - 1) % 5]:
            _render_pick_card(row, idx)

    b1, b2 = st.columns(2)
    with b1:
        st.markdown("#### Top Opportunities")
        opp = df[df["final_score"] > 50].sort_values("final_score", ascending=False)
        if opp.empty:
            st.info("No strong opportunities in this run.")
        else:
            show_cols = ["stock_symbol", "final_score", "recommendation", "explanation"]
            st.dataframe(opp[show_cols].head(10), use_container_width=True, height=310)
    with b2:
        st.markdown("#### Top Risks")
        risks = df[df["final_score"] < 50].sort_values("final_score", ascending=True)
        if risks.empty:
            st.info("No major downside risks in this run.")
        else:
            show_cols = ["stock_symbol", "final_score", "recommendation", "explanation"]
            st.dataframe(risks[show_cols].head(10), use_container_width=True, height=310)

    alerts = db.fetch_latest_alerts(limit=10)
    st.markdown("#### Latest Alerts")
    if not alerts:
        st.info("No alerts generated in latest run.")
    else:
        for alert in alerts:
            st.write(f"- [{alert.get('alert_type', 'ALERT')}] {alert.get('stock_symbol', '')}: {alert.get('message', '')}")

    if not beginner_mode:
        with st.expander("Advanced Table"):
            cols = [
                "rank",
                "stock_symbol",
                "final_score",
                "recommendation",
                "raw_score",
                "confidence_multiplier",
                "recency_multiplier",
                "explanation",
            ]
            st.dataframe(df[cols].sort_values("rank").head(50), use_container_width=True, height=420)


def render_explain_stock(db: Any, beginner_mode: bool) -> None:
    st.subheader("Explain Stock")
    st.caption("Pick a stock to understand the exact reason behind its score.")

    rows = db.fetch_latest_scores(limit=2000)
    df = _prepare_scores(rows)
    if df.empty:
        st.info("No stock score data available.")
        return

    df = df.sort_values(["action_score", "rank"], ascending=[False, True])
    options = [
        f"{row.stock_symbol} | score {row.final_score:.1f} | {row.recommendation}"
        for row in df.itertuples()
    ]
    selected = st.selectbox("Choose stock", options=options, index=0)
    idx = options.index(selected)
    stock_id = str(df.iloc[idx]["stock_id"])
    selected_row = df.iloc[idx]

    detail = db.fetch_stock_detail(stock_id)
    score = detail.get("score")
    if not score:
        st.info("No detail available for selected stock.")
        return

    score_value = float(score.get("final_score", 50.0))
    reco = _recommendation(score_value)
    confidence_label = str(selected_row.get("confidence_band", "Low"))

    c1, c2, c3, c4 = st.columns([1.0, 1.2, 1.2, 2.0])
    with c1:
        st.metric("Rank", f"#{int(score.get('rank', 0))}")
    with c2:
        st.metric("Score", f"{score_value:.1f}")
        st.markdown(_recommendation_chip(reco), unsafe_allow_html=True)
    with c3:
        st.metric("Confidence", confidence_label)
        st.progress(_confidence_meter_value(confidence_label))
    with c4:
        st.markdown("**Why this score?**")
        st.write(score.get("explanation", "No explanation available."))

    comp = {
        "insider_score": score.get("insider_score", 0),
        "earnings_score": score.get("earnings_score", 0),
        "sentiment_score": score.get("sentiment_score", 0),
        "event_score": score.get("event_score", 0),
        "density_bonus": score.get("density_bonus", 0),
        "confidence_multiplier": score.get("confidence_multiplier", 1),
        "recency_multiplier": score.get("recency_multiplier", 1),
    }

    if beginner_mode:
        st.markdown("#### Quick Breakdown")
        st.write(f"- Insider impact: {comp['insider_score']:+.1f}")
        st.write(f"- Earnings impact: {comp['earnings_score']:+.1f}")
        st.write(f"- Sentiment impact: {comp['sentiment_score']:+.1f}")
        st.write(f"- Event impact: {comp['event_score']:+.1f}")
        st.write(
            f"- Confidence x{float(comp['confidence_multiplier']):.2f}, "
            f"Recency x{float(comp['recency_multiplier']):.2f}"
        )
    else:
        cdf = pd.DataFrame({"component": list(comp.keys()), "value": list(comp.values())})
        st.dataframe(cdf, use_container_width=True, hide_index=True)

    signal_df = _safe_df(detail.get("signals", []))
    event_df = _safe_df(detail.get("events", []))

    # --- Signal Timeline ---
    if not signal_df.empty:
        st.markdown("#### Signal Timeline")
        timeline_items = []
        for _, sig in signal_df.head(8).iterrows():
            s_type = str(sig.get("signal_type", "")).replace("_", " ").title()
            strength = float(sig.get("strength", 0))
            ts = str(sig.get("event_ts", ""))[:10]
            if strength > 0:
                border_color = "rgba(74,222,128,0.4)"
                icon = "🟢"
            elif strength < 0:
                border_color = "rgba(248,113,113,0.4)"
                icon = "🔴"
            else:
                border_color = "rgba(148,163,184,0.3)"
                icon = "⚪"
            timeline_items.append(
                f"<div class='or-tl-item' style='border-color:{border_color}'>"
                f"<div>{icon} {s_type}</div>"
                f"<div style='font-weight:600;margin-top:0.2rem'>{strength:+.0f}</div>"
                f"<div style='color:#8ba4c8;font-size:0.72rem;margin-top:0.15rem'>{ts}</div>"
                f"</div>"
            )
        st.markdown(
            "<div class='or-timeline'>" + "".join(timeline_items) + "</div>",
            unsafe_allow_html=True,
        )

    with st.expander("Signals Used"):
        if signal_df.empty:
            st.info("No recent signals found.")
        else:
            keep = [
                col
                for col in ["event_ts", "signal_type", "category", "strength", "confidence", "explanation"]
                if col in signal_df.columns
            ]
            st.dataframe(signal_df[keep].head(12), use_container_width=True, height=280)

    with st.expander("Recent Events"):
        if event_df.empty:
            st.info("No recent events found.")
        else:
            keep = [
                col
                for col in ["timestamp", "event_type", "sentiment", "magnitude", "confidence", "raw_text"]
                if col in event_df.columns
            ]
            st.dataframe(event_df[keep].head(12), use_container_width=True, height=280)


def render_ai_copilot(db: Any, settings: Any) -> None:
    st.subheader("Ask AI")
    st.caption("Ask in plain English. The assistant answers only from latest radar facts.")

    llm = OpenRouterClient(settings)

    st.markdown("#### Common Terms")
    term_cols = st.columns(3)
    for idx, term in enumerate(TERM_EXPLANATIONS.keys()):
        with term_cols[idx % 3]:
            if st.button(term, key=f"term_{term}", use_container_width=True):
                st.session_state["selected_term"] = term

    selected_term = st.session_state.get("selected_term")
    if selected_term and selected_term in TERM_EXPLANATIONS:
        st.info(f"{selected_term}: {TERM_EXPLANATIONS[selected_term]}")

    st.markdown("#### Quick Question Buttons")
    quick_cols = st.columns(2)
    for idx, q in enumerate(QUICK_QUESTIONS):
        with quick_cols[idx % 2]:
            if st.button(q, key=f"quick_q_{idx}", use_container_width=True):
                st.session_state["ai_question"] = q

    question = st.text_area(
        "Your question",
        value=st.session_state.get("ai_question", "Which stocks look strongest today and why?"),
        height=100,
    ).strip()

    if st.button("Ask", type="primary"):
        if not question:
            st.info("Please type a question.")
            return

        rows = db.fetch_latest_scores(limit=max(settings.max_stock_universe, 120))
        df = _prepare_scores(rows)
        alerts = db.fetch_latest_alerts(limit=20)

        facts = _fact_pack_for_ai(df, alerts)
        answer = llm.answer_user_query(question, facts) if llm.enabled else None
        if not answer:
            answer = _deterministic_query_answer(question, df, alerts)

        st.markdown("#### Answer")
        st.write(answer)

        with st.expander("Facts used for this answer"):
            for fact in facts:
                st.write(f"- {fact}")


def render_watchlist_digest(db: Any, settings: Any) -> None:
    st.subheader("Watchlist & Digest")
    st.caption("Each login has its own persistent watchlist. Add stocks by typing or dropdown and receive Telegram updates.")

    active_user_id = str(st.session_state.get("active_user_id", "")).strip()
    if not active_user_id:
        st.info("Login from the left sidebar to create your personal watchlist.")
        return

    profile = db.fetch_user_profile(active_user_id)
    default_name = str((profile or {}).get("display_name") or active_user_id)
    default_chat_id = str((profile or {}).get("telegram_chat_id") or "")

    def _save_profile() -> None:
        new_name = st.session_state.get("inp_name", "").strip() or default_name
        new_chat = st.session_state.get("inp_chat_id", "").strip() or None
        db.upsert_user_profile(active_user_id, new_name, new_chat)

    p1, p2 = st.columns([1.2, 1.2])
    with p1:
        user_name = st.text_input("Display Name", value=default_name, key="inp_name", on_change=_save_profile).strip() or default_name
    with p2:
        telegram_chat_id = st.text_input(
            "Telegram Chat ID (Auto-saves)",
            value=default_chat_id,
            key="inp_chat_id",
            on_change=_save_profile,
            help="Find this by messaging your bot. Once entered, updates are completely automatic!",
        ).strip()
        
    if telegram_chat_id:
        st.success("✅ Telegram Chat ID saved! You will automatically receive the Alpha Digest every time a scan finishes.")

    st.caption(f"Logged in as: {active_user_id}")

    rows = db.fetch_latest_scores(limit=max(settings.max_stock_universe, 200))
    score_df = _prepare_scores(rows)
    if score_df.empty:
        st.info("No latest scores available. Run a fresh scan first.")
        return

    universe_symbols = sorted(set(score_df["stock_symbol"].astype(str).str.upper().tolist()))
    a1, a2, a3 = st.columns([1.0, 1.3, 0.8])
    with a1:
        typed_symbol = _normalize_symbol(st.text_input("Type stock symbol", value=""))
    with a2:
        picked_symbols = st.multiselect(
            "Or pick from dropdown",
            options=universe_symbols,
            default=[],
        )
    with a3:
        if st.button("Add to Watchlist", type="primary", use_container_width=True):
            to_add = sorted(set(([typed_symbol] if typed_symbol else []) + picked_symbols))
            inserted = db.add_user_watchlist_symbols(active_user_id, to_add)
            if inserted > 0:
                st.success(f"Added {inserted} symbol(s).")
            else:
                st.info("Type or select at least one symbol.")

    symbols = db.fetch_user_watchlist_symbols(active_user_id)
    c1, c2 = st.columns([1.2, 1.0])
    with c1:
        st.write(f"Watchlist symbols loaded: {len(symbols)}")
    with c2:
        if st.button("Clear Watchlist", use_container_width=True):
            db.clear_user_watchlist(active_user_id)
            st.success("Watchlist cleared.")
            symbols = []

    if symbols:
        r1, r2 = st.columns([1.2, 0.8])
        with r1:
            st.write("Current symbols: " + ", ".join(symbols))
        with r2:
            remove_symbol = st.selectbox("Remove one", options=symbols, index=0)
            if st.button("Remove Symbol", use_container_width=True):
                db.remove_user_watchlist_symbol(active_user_id, remove_symbol)
                st.success(f"Removed {remove_symbol}.")
                symbols = db.fetch_user_watchlist_symbols(active_user_id)

    if symbols:
        watch_df = score_df[score_df["stock_symbol"].astype(str).str.upper().isin(set(symbols))].copy()
    else:
        watch_df = pd.DataFrame()

    missing = sorted(set(symbols) - set(score_df["stock_symbol"].astype(str).str.upper().tolist()))
    if missing:
        st.warning("These symbols were not found in current universe: " + ", ".join(missing[:20]))

    st.markdown("#### Watchlist Alerts")
    watch_alerts = build_watchlist_alert_rows(score_df.to_dict(orient="records"), symbols)
    if not watch_alerts:
        st.info("No watchlist alerts yet. Add symbols and run a fresh scan.")
    else:
        watch_alert_df = pd.DataFrame(watch_alerts)
        st.dataframe(
            watch_alert_df[["stock_symbol", "score", "recommendation", "confidence", "alert_type", "message"]],
            use_container_width=True,
            height=300,
        )

    latest_alerts = db.fetch_latest_alerts(limit=12)
    digest = _build_telegram_digest(user_name, score_df, latest_alerts, watch_alerts)

    st.markdown("#### Telegram Updates")
    auto_status = "ON" if settings.telegram_updates_enabled and bool(settings.telegram_bot_token.strip()) else "OFF"
    st.caption(f"Automatic Telegram updates after each scan: {auto_status}")

    send_now = st.button("Test Telegram Notification", use_container_width=False)
    if send_now:
        if not telegram_chat_id:
            st.warning("Please enter your Telegram Chat ID above first.")
        elif not settings.telegram_bot_token.strip():
            st.warning("Telegram bot token is missing in .env (TELEGRAM_BOT_TOKEN).")
        else:
            message = digest
            ok, error = send_telegram_message(settings, telegram_chat_id, message)
            if ok:
                db.log_user_notification(
                    user_id=active_user_id,
                    run_date=datetime.utcnow().isoformat(),
                    channel="telegram",
                    status="sent",
                    message=f"Manual watchlist update sent for {len(watch_alerts)} symbols.",
                )
                st.success("Telegram update sent.")
            else:
                db.log_user_notification(
                    user_id=active_user_id,
                    run_date=datetime.utcnow().isoformat(),
                    channel="telegram",
                    status="failed",
                    message=(error or "Unknown Telegram error")[:400],
                )
                st.error(f"Failed to send Telegram update: {error}")


def render_project_info(db: Any, settings: Any) -> None:
    st.subheader("Project Info")

    ai_mode = "AI enabled" if bool(settings.openrouter_api_key) and not (
        settings.fast_mode and settings.disable_openrouter_in_fast_mode
    ) else "Deterministic mode"

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Database", str(getattr(db, "backend_name", "unknown")))
    m2.metric("Scan Mode", "Fast" if settings.fast_mode else "Full")
    m3.metric("Universe", "Curated" if settings.use_curated_universe else "Full")
    m4.metric("Reasoning", ai_mode)

    if getattr(db, "backend_name", "") == "sqlite":
        st.info("Running on local SQLite fallback. Supabase is available once credentials/schema are fully ready.")

    st.markdown("#### What each button does")
    st.write("- Run Fresh Scan: fetches latest market events and recalculates scores.")
    st.write("- Dashboard: shows today's top opportunities and top risks.")
    st.write("- Explain Stock: tells why one stock got its score.")
    st.write("- Ask AI: answers your question using latest radar facts.")
    st.write("- Watchlist & Digest: manage personal watchlist by login, get alerts, and Telegram updates.")

    rows = db.fetch_recent_raw_events(limit=15)
    df = _safe_df(rows)
    st.markdown("#### Latest Raw Events (sample)")
    if df.empty:
        st.info("No raw events available.")
    else:
        keep = [col for col in ["published_at", "source", "stock_symbol", "title"] if col in df.columns]
        st.dataframe(df[keep], use_container_width=True, height=260)


def render_ai_explains(db: Any, settings: Any) -> None:
    st.subheader("🧠 AI Explains")
    st.markdown(
        "<div class='or-glass'>"
        "<b>Ask anything about the market.</b> The AI answers using your latest radar data — "
        "scores, signals, alerts, and portfolio context. No hallucinations, only facts from your scan."
        "</div>",
        unsafe_allow_html=True,
    )

    llm = OpenRouterClient(settings)

    if "ai_chat_history" not in st.session_state:
        st.session_state["ai_chat_history"] = []

    # Quick question chips
    chip_questions = [
        "Which stocks look strongest today?",
        "What are the biggest risks right now?",
        "Explain the top 3 opportunities with reasoning.",
        "Any insider buying activity worth noting?",
        "Summarize today's market signals.",
        "Which stocks have the highest confidence scores?",
    ]
    st.markdown("#### Quick Questions")
    chip_cols = st.columns(3)
    for idx, q in enumerate(chip_questions):
        with chip_cols[idx % 3]:
            if st.button(q, key=f"ai_explain_chip_{idx}", use_container_width=True):
                st.session_state["ai_explain_input"] = q

    question = st.text_area(
        "Your question",
        value=st.session_state.get("ai_explain_input", ""),
        height=80,
        placeholder="e.g. Why is RELIANCE scored high today?",
    ).strip()

    c1, c2 = st.columns([1, 4])
    with c1:
        ask_btn = st.button("🚀 Ask AI", type="primary", use_container_width=True)
    with c2:
        if st.button("Clear History", use_container_width=False):
            st.session_state["ai_chat_history"] = []

    if ask_btn and question:
        rows = db.fetch_latest_scores(limit=max(settings.max_stock_universe, 120))
        df = _prepare_scores(rows)
        alerts = db.fetch_latest_alerts(limit=20)
        facts = _fact_pack_for_ai(df, alerts)

        answer = llm.answer_user_query(question, facts) if llm.enabled else None
        if not answer:
            answer = _deterministic_query_answer(question, df, alerts)

        st.session_state["ai_chat_history"].append({"q": question, "a": answer})
        st.session_state["ai_explain_input"] = ""

    # Render chat history
    history = st.session_state.get("ai_chat_history", [])
    if history:
        st.markdown("#### Conversation")
        for entry in history:
            st.markdown(
                f"<div class='or-chat-user'><b>You:</b> {entry['q']}</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div class='or-chat-ai'><b>🤖 Radar AI:</b><br/>{entry['a']}</div>",
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            "<div class='or-glass' style='text-align:center;color:#8ba4c8;padding:2rem'>"
            "Ask a question above to start a conversation with Radar AI."
            "</div>",
            unsafe_allow_html=True,
        )

    # Term glossary
    with st.expander("📖 Market Terms Glossary"):
        for term, explanation in TERM_EXPLANATIONS.items():
            st.write(f"**{term}**: {explanation}")


def render_login_page(db: Any) -> bool:
    """Render full-screen login. Returns True if user is logged in."""
    if st.session_state.get("logged_in"):
        return True

    st.markdown(
        """
        <div class='or-login-container'>
            <div style='font-size:3rem;margin-bottom:0.3rem'>⚡</div>
            <div class='or-login-title'>Opportunity Radar</div>
            <div class='or-login-subtitle'>
                AI-powered signal intelligence for Indian equities
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("login_page_form"):
            user_id = st.text_input("User ID", value="investor", help="Your unique login identifier")
            display_name = st.text_input("Display Name", value="Investor", help="How your name appears in reports")
            submitted = st.form_submit_button("🚀 Enter Radar", use_container_width=True, type="primary")

        if submitted and user_id.strip():
            normalized = _normalize_user_id(user_id)
            name = str(display_name or normalized).strip() or normalized
            try:
                db.upsert_user_profile(normalized, name)
            except RuntimeError as error:
                st.error(str(error))
                return False
            st.session_state["logged_in"] = True
            st.session_state["active_user_id"] = normalized
            st.session_state["active_display_name"] = name
            st.rerun()
        return False


def _render_sidebar_market_pulse(db: Any, settings: Any) -> None:
    """Show a quick market pulse in the sidebar with live prices of top stocks."""
    try:
        rows = db.fetch_latest_scores(limit=5)
        if not rows:
            return
        st.sidebar.markdown("#### 📡 Market Pulse")
        for row in rows[:3]:
            symbol = str(row.get("stock_symbol", ""))
            score = float(row.get("final_score", 50.0) or 50.0)
            live = get_live_price(symbol)
            if live:
                pct = live["change_pct"]
                arrow = "▲" if pct >= 0 else "▼"
                color = "#4ade80" if pct >= 0 else "#f87171"
                st.sidebar.markdown(
                    f"<div class='or-market-pulse'>"
                    f"<b>{symbol}</b> • Score: {score:.0f}<br/>"
                    f"₹{live['price']:,.2f} <span style='color:{color}'>{arrow} {abs(pct):.2f}%</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.sidebar.markdown(
                    f"<div class='or-market-pulse'>"
                    f"<b>{symbol}</b> • Score: {score:.0f}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    except Exception:
        pass


def render_signal_heatmap(db: Any) -> None:
    """Unique feature: Signal strength heatmap across all scanned stocks."""
    st.subheader("🔥 Signal Heatmap")
    st.markdown(
        "<div class='or-glass'>"
        "Visual overview of signal strength across your entire universe. "
        "Brighter colors = stronger conviction. Spot patterns at a glance."
        "</div>",
        unsafe_allow_html=True,
    )

    rows = db.fetch_latest_scores(limit=200)
    df = _prepare_scores(rows)
    if df.empty:
        st.info("No scored data. Run a fresh scan first.")
        return

    df = df.sort_values("final_score", ascending=False)

    # Build heatmap grid
    st.markdown("#### Score Distribution")
    opp_count = int((df["zone"] == "Opportunity").sum())
    risk_count = int((df["zone"] == "Risk").sum())
    neutral_count = int((df["zone"] == "Neutral").sum())

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.metric("Total Stocks", len(df))
    with k2:
        st.metric("🟢 Opportunities", opp_count)
    with k3:
        st.metric("🔴 Risks", risk_count)
    with k4:
        st.metric("🟡 Neutral", neutral_count)

    # Visual heatmap blocks
    heatmap_items = []
    for _, row in df.head(50).iterrows():
        score = float(row.get("final_score", 50.0))
        symbol = str(row.get("stock_symbol", ""))
        if score >= 65:
            bg = "rgba(34,197,94,0.4)"
            border = "rgba(74,222,128,0.5)"
        elif score >= 55:
            bg = "rgba(59,130,246,0.3)"
            border = "rgba(96,165,250,0.4)"
        elif score <= 35:
            bg = "rgba(239,68,68,0.4)"
            border = "rgba(248,113,113,0.5)"
        elif score <= 45:
            bg = "rgba(245,158,11,0.3)"
            border = "rgba(251,191,36,0.4)"
        else:
            bg = "rgba(100,116,139,0.2)"
            border = "rgba(148,163,184,0.3)"
        heatmap_items.append(
            f"<div style='display:inline-block;padding:0.4rem 0.6rem;margin:3px;"
            f"border-radius:8px;background:{bg};border:1px solid {border};"
            f"font-size:0.78rem;font-weight:600;color:#fff;cursor:default;"
            f"transition:transform 0.2s' title='{symbol}: {score:.1f}'>"
            f"{symbol}<br/><span style='font-size:0.72rem;opacity:0.9'>{score:.0f}</span>"
            f"</div>"
        )
    st.markdown(
        "<div style='line-height:2.8'>" + "".join(heatmap_items) + "</div>",
        unsafe_allow_html=True,
    )

    # Sector-like breakdown by score range
    st.markdown("#### Score Breakdown Table")
    breakdown_data = []
    for _, row in df.iterrows():
        breakdown_data.append({
            "Symbol": str(row.get("stock_symbol", "")),
            "Score": round(float(row.get("final_score", 50.0)), 1),
            "Zone": str(row.get("zone", "Neutral")),
            "Recommendation": str(row.get("recommendation", "Watch")),
            "Confidence": str(row.get("confidence_band", "Low")),
        })
    st.dataframe(pd.DataFrame(breakdown_data), use_container_width=True, height=400)


def render_alpha_finder(db: Any) -> None:
    st.subheader("🚀 Deep Signal Radar (Alpha Finder)")
    st.markdown(
        "<div class='or-help'>"
        "<b>What is this?</b> Our proprietary engine scans the entire universe for <b>Hidden Signal Convergence</b>. "
        "We look for stocks where multiple distinct institutional triggers (e.g., Bulk Deals + Insider Buying + Regulatory/Earnings) "
        "fire simultaneously. These are rare, high-conviction money-making setups that typical summarizers miss."
        "</div>",
        unsafe_allow_html=True,
    )

    rows = db.fetch_latest_scores(limit=2000)
    df = _prepare_scores(rows)
    if df.empty:
        st.info("No stock data available.")
        return

    df["density_bonus"] = pd.to_numeric(df.get("density_bonus"), errors="coerce").fillna(0)
    df["insider_score"] = pd.to_numeric(df.get("insider_score"), errors="coerce").fillna(0)
    df["event_score"] = pd.to_numeric(df.get("event_score"), errors="coerce").fillna(0)
    
    alpha_df = df[(df["final_score"] >= 60) & (df["density_bonus"] >= 5)].sort_values("final_score", ascending=False)
    
    if alpha_df.empty:
        st.warning("No Deep Convergence Signals detected in the latest scan. The market is quiet on the institutional front.")
        return

    st.success(f"🔥 Found {len(alpha_df)} High-Conviction Setups!")

    for idx, row in enumerate(alpha_df.itertuples(), start=1):
        score = float(getattr(row, "final_score", 50.0))
        symbol = str(getattr(row, "stock_symbol", ""))
        expl = str(getattr(row, "explanation", ""))
        insider = float(getattr(row, "insider_score", 0.0))
        event = float(getattr(row, "event_score", 0.0))
        density = float(getattr(row, "density_bonus", 0.0))
        
        tags = []
        if density >= 10:
            tags.append("💥 3+ Signals Aligned")
        elif density >= 5:
            tags.append("⚡ 2 Signals Aligned")
            
        if insider > 5:
            tags.append("🤵 Insider Accumulation")
        if event > 5:
            tags.append("📰 Institutional Catalyst")
            
        tag_html = " &bull; ".join(f"<span style='color:#a78bfa'>{t}</span>" for t in tags)
        
        st.markdown(
            f"<div class='or-glass'>"
            f"<b style='font-size:1.2rem;color:#4ade80'>{symbol}</b> &bull; "
            f"<span style='font-size:1.1rem;font-weight:600'>Score: {score:.1f}</span><br/>"
            f"<div style='margin-top:0.2rem'>{tag_html}</div>"
            f"<div style='margin-top:0.5rem;font-size:0.9rem;color:#e8eef8;line-height:1.4'>{expl}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )


def main() -> None:
    _inject_styles()
    settings = get_settings()
    db = get_db()

    ok, message = db.validate_connection()
    if not ok:
        st.error(message or "Unable to connect to database.")
        return

    # --- Login Gate ---
    if not render_login_page(db):
        return

    # --- Hero Banner ---
    st.markdown(
        """
        <div class='or-hero'>
            <h1 style='margin-bottom:0.2rem;position:relative;z-index:1;color:#ffffff !important'>⚡ Opportunity Radar</h1>
            <p style='margin:0;color:#e8f0ff;position:relative;z-index:1'>
                <span class='or-pulse'></span>
                AI-powered signal intelligence for Indian equities — from filings to actionable alpha.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # --- Sidebar ---
    active_user_id = str(st.session_state.get("active_user_id", "investor"))
    active_display_name = str(st.session_state.get("active_display_name", "Investor"))

    st.sidebar.markdown(
        f"<div style='padding:0.5rem;border-radius:10px;background:rgba(60,130,246,0.12);"
        f"border:1px solid rgba(60,130,246,0.25);margin-bottom:0.8rem;text-align:center'>"
        f"<div style='font-weight:700;font-size:1rem;color:#ffffff'>👤 {active_display_name}</div>"
        f"<div style='font-size:0.78rem;color:#a8bcd4;margin-top:0.2rem'>ID: {active_user_id}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    st.sidebar.caption(
        f"Mode: {'fast' if settings.fast_mode else 'full'} | DB: {getattr(db, 'backend_name', 'unknown')}"
    )

    # Ensure user profile exists
    try:
        active_profile = db.fetch_user_profile(active_user_id)
        if not active_profile:
            active_profile = db.upsert_user_profile(active_user_id, active_display_name)
    except RuntimeError as error:
        st.error(str(error))
        return
    st.session_state["active_display_name"] = str(active_profile.get("display_name") or active_user_id)

    # Market pulse
    _render_sidebar_market_pulse(db, settings)

    beginner_mode = st.sidebar.toggle("Beginner Mode", value=True)

    page = st.sidebar.radio(
        "📍 Navigate",
        options=["Dashboard", "Explain Stock", "AI Explains", "Alpha Signal Finder", "Signal Heatmap", "Watchlist & Alerts", "Project Info"],
    )

    if st.sidebar.button("🚪 Logout", use_container_width=True):
        st.session_state["logged_in"] = False
        st.session_state.pop("active_user_id", None)
        st.session_state.pop("active_display_name", None)
        st.rerun()

    try:
        if page == "Dashboard":
            render_dashboard(db, settings, beginner_mode)
        elif page == "Explain Stock":
            render_explain_stock(db, beginner_mode)
        elif page == "AI Explains":
            render_ai_explains(db, settings)
        elif page == "Alpha Signal Finder":
            render_alpha_finder(db)
        elif page == "Signal Heatmap":
            render_signal_heatmap(db)
        elif page == "Watchlist & Alerts":
            render_watchlist_digest(db, settings)
        else:
            render_project_info(db, settings)
    except RuntimeError as error:
        st.error(str(error))


if __name__ == "__main__":
    main()

