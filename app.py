import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timedelta
from rapidfuzz import fuzz, process
import time
import random
import io

# ─── Google Sheet Mapping ─────────────────────────────────────────────────────
SHEET_ID = "14tUgMxJI_glunJiyg8F61D5oduorWJRBRzHfT8zGRtU"

@st.cache_data(ttl=300, show_spinner=False)
def load_mapping_from_sheet(sheet_id: str) -> tuple[pd.DataFrame, str]:
    """
    Load competition → sofascore_tournament_id mapping from Google Sheet.
    Returns (DataFrame, error_msg).
    Sheet columns expected: competition_id, sofascore_tournament_id (+ others optional)
    """
    # Try each tab by gid — gid=0 is Master tab
    for gid in ["0"]:
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                df = pd.read_csv(io.StringIO(resp.text))
                df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
                return df, ""
            elif resp.status_code == 403:
                return pd.DataFrame(), "الـ Sheet مش public — تأكد من إعدادات المشاركة"
        except Exception as e:
            return pd.DataFrame(), str(e)
    return pd.DataFrame(), "تعذّر جلب الـ Sheet"

st.set_page_config(
    page_title="Match Comparator ⚽",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
[data-testid="stAppViewContainer"] { background: #0f1117; }
[data-testid="stSidebar"] { background: #161b27; border-right: 1px solid #2a3147; }
div[data-testid="stDataFrameResizable"] { border: 1px solid #2a3147; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

# ─── Headers pool — rotate to avoid blocks ───────────────────────────────────
UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

def get_headers():
    return {
        "User-Agent": random.choice(UA_POOL),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.sofascore.com/",
        "Origin": "https://www.sofascore.com",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "keep-alive",
    }

SOFASCORE_BASE = "https://api.sofascore.com/api/v1"

DEFAULT_MAPPINGS = {
    "Côte d'Ivoire": "Ivory Coast",
    "Congo DR": "DR Congo",
    "Korea Republic": "South Korea",
    "Korea DPR": "North Korea",
    "São Tomé e Príncipe": "Sao Tome and Principe",
    "Bosnia & Herzegovina": "Bosnia and Herzegovina",
    "Chinese Taipei": "Taiwan",
    "Kyrgyz Republic": "Kyrgyzstan",
    "Brazil - Seria A": "Brasileirão Série A",
    "Turkey Superliga": "Süper Lig",
    "Champions League": "UEFA Champions League",
    "PRIMERA DIVISIÓN": "Primera División",
    "Stoiximan Super League": "Super League",
    "Denmark Superliga": "Superliga",
    "Switzerland Super League": "Super League",
    "Chance Liga": "Czech First League",
}

# ─── SofaScore fetcher with retry ────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def fetch_sofascore_day(date_str: str) -> tuple[list, str]:
    """Returns (rows, error_msg). error_msg is empty string on success."""
    url = f"{SOFASCORE_BASE}/sport/football/scheduled-events/{date_str}"

    for attempt in range(3):
        try:
            if attempt > 0:
                time.sleep(1.5 * attempt)
            resp = requests.get(url, headers=get_headers(), timeout=20)

            if resp.status_code == 403:
                return [], "403_blocked"
            if resp.status_code == 429:
                time.sleep(3)
                continue
            resp.raise_for_status()

            data = resp.json()
            events = data.get("events", [])
            rows = []
            for e in events:
                try:
                    ts = e.get("startTimestamp", 0)
                    kickoff = datetime.utcfromtimestamp(ts).strftime("%H:%M") if ts else ""
                    rows.append({
                        "sofascore_id": e.get("id", ""),
                        "home_team": e.get("homeTeam", {}).get("name", ""),
                        "away_team": e.get("awayTeam", {}).get("name", ""),
                        "tournament": e.get("tournament", {}).get("name", ""),
                        "category": e.get("tournament", {}).get("category", {}).get("name", ""),
                        "match_date": date_str,
                        "kick_off_time": kickoff,
                        "status": e.get("status", {}).get("description", ""),
                    })
                except Exception:
                    continue
            return rows, ""

        except requests.exceptions.ConnectionError:
            return [], "connection_error"
        except requests.exceptions.Timeout:
            if attempt == 2:
                return [], "timeout"
            continue
        except Exception as ex:
            return [], str(ex)

    return [], "max_retries"


def fetch_sofascore_range(date_from: datetime, date_to: datetime):
    """Returns (DataFrame, error_type). error_type: '' | '403_blocked' | other"""
    all_rows = []
    total_days = (date_to - date_from).days + 1
    progress = st.progress(0, text="جاري جلب بيانات SofaScore...")

    for i in range(total_days):
        current = date_from + timedelta(days=i)
        date_str = current.strftime("%Y-%m-%d")
        progress.progress((i + 1) / total_days, text=f"جلب {date_str}...")

        rows, err = fetch_sofascore_day(date_str)
        if err == "403_blocked":
            progress.empty()
            return pd.DataFrame(), "403_blocked"
        if err:
            progress.empty()
            return pd.DataFrame(), err

        all_rows.extend(rows)
        time.sleep(0.4 + random.uniform(0, 0.3))

    progress.empty()
    return (pd.DataFrame(all_rows) if all_rows else pd.DataFrame()), ""


# ─── Parse SofaScore CSV export ──────────────────────────────────────────────
def parse_sofascore_csv(uploaded_file) -> tuple[pd.DataFrame, str]:
    """
    Handle different SofaScore CSV/export formats.
    Returns (DataFrame with standard columns, error_msg)
    """
    try:
        df = pd.read_csv(uploaded_file)
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

        # Map common column name variants
        col_map = {
            # home team
            "home": "home_team", "home_team_name": "home_team", "hometeam": "home_team",
            "home team": "home_team", "local": "home_team",
            # away team
            "away": "away_team", "away_team_name": "away_team", "awayteam": "away_team",
            "away team": "away_team", "visitor": "away_team",
            # date
            "date": "match_date", "game_date": "match_date", "event_date": "match_date",
            "start_date": "match_date",
            # time
            "time": "kick_off_time", "kickoff": "kick_off_time", "start_time": "kick_off_time",
            "kick_off": "kick_off_time", "ko": "kick_off_time",
            # tournament
            "league": "tournament", "competition": "tournament",
            "tournament_name": "tournament", "league_name": "tournament",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        required = ["home_team", "away_team", "match_date"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            return pd.DataFrame(), f"الأعمدة دي مش موجودة: {', '.join(missing)}\nالأعمدة الموجودة: {', '.join(df.columns.tolist())}"

        # Normalize date
        df["match_date"] = pd.to_datetime(df["match_date"], errors="coerce").dt.strftime("%Y-%m-%d")
        if "kick_off_time" not in df.columns:
            df["kick_off_time"] = ""
        if "tournament" not in df.columns:
            df["tournament"] = ""

        df["kick_off_time"] = df["kick_off_time"].astype(str).str[:5]
        return df, ""

    except Exception as ex:
        return pd.DataFrame(), str(ex)


# ─── Comparison engine ────────────────────────────────────────────────────────
def normalize(name: str, mappings: dict) -> str:
    n = str(name).strip()
    n = mappings.get(n, n)
    return n.lower().strip()


def safe_date(val) -> str:
    """Parse any date format and return YYYY-MM-DD."""
    try:
        return pd.to_datetime(str(val), dayfirst=False).strftime("%Y-%m-%d")
    except Exception:
        try:
            return pd.to_datetime(str(val), dayfirst=True).strftime("%Y-%m-%d")
        except Exception:
            return str(val)


def apply_tz_offset(df: pd.DataFrame, offset_hours: int) -> pd.DataFrame:
    """
    Shift kick_off_time in SofaScore data by offset_hours to convert UTC → local.
    Also adjusts match_date when time crosses midnight.
    """
    if offset_hours == 0:
        return df
    df = df.copy()

    def shift_row(row):
        t = str(row.get("kick_off_time", ""))[:5]
        d = str(row.get("match_date", ""))
        if not t or len(t) < 5 or ":" not in t:
            return row
        try:
            h, m = int(t[:2]), int(t[3:5])
            total_min = h * 60 + m + offset_hours * 60
            # Handle day crossover
            day_shift = total_min // (24 * 60)
            total_min = total_min % (24 * 60)
            if total_min < 0:
                total_min += 24 * 60
                day_shift -= 1
            new_h = total_min // 60
            new_m = total_min % 60
            row["kick_off_time"] = f"{new_h:02d}:{new_m:02d}"
            if day_shift != 0:
                try:
                    new_date = datetime.strptime(d, "%Y-%m-%d") + timedelta(days=day_shift)
                    row["match_date"] = new_date.strftime("%Y-%m-%d")
                except Exception:
                    pass
        except Exception:
            pass
        return row

    return df.apply(shift_row, axis=1)


def build_sf_mapping(mapping_df: pd.DataFrame) -> dict:
    """
    competition_id (str) → set({sofascore_tournament_id, ...})
    Handles: single IDs, comma-separated, newline-separated, NaN, 'Not in Sofascore'
    """
    if mapping_df.empty:
        return {}

    import re

    cols = mapping_df.columns.tolist()

    # Find columns — exact match first, then partial
    comp_id_col = next((c for c in cols if c == 'competition_id'), None) or \
                  next((c for c in cols if 'competition_id' in c), None)
    sf_id_col   = next((c for c in cols if c == 'sofascore_tournament_id'), None) or \
                  next((c for c in cols if 'sofascore' in c and 'id' in c and 'name' not in c), None)

    if not comp_id_col or not sf_id_col:
        return {}

    result = {}
    for _, row in mapping_df.iterrows():
        # competition_id
        cid_raw = row.get(comp_id_col, '')
        if pd.isna(cid_raw) or str(cid_raw).strip() in ('', 'nan', 'None'):
            continue
        cid = str(int(float(str(cid_raw).strip()))) if str(cid_raw).strip().replace('.','').isdigit() else str(cid_raw).strip()

        # sofascore_tournament_id — may be multiple values
        sid_raw = row.get(sf_id_col, '')
        if pd.isna(sid_raw):
            continue
        raw = str(sid_raw).strip()
        if not raw or raw.lower() in ('nan', 'none', 'not in sofascore', 'n/a', '-'):
            continue

        # Split on comma, newline, semicolon, space
        parts = re.split(r'[,\n;\s]+', raw)
        ids = set()
        for p in parts:
            p = p.strip().replace('.0', '')  # handle float like "17.0"
            if p and p.isdigit():
                ids.add(p)

        if ids:
            result.setdefault(cid, set()).update(ids)

    return result


def compare(db_df: pd.DataFrame, sf_df: pd.DataFrame,
            competition_filter: list, fuzzy_threshold: int,
            mappings: dict, exclude_cancelled: bool,
            tz_offset: int = 0,
            tracked_comp_ids: set = None,
            sf_mapping: dict = None) -> pd.DataFrame:
    """
    competition_filter  : أسماء البطولات المختارة من ملف الماتشات
    tracked_comp_ids    : set of competition_id من competitions_2026.csv
    sf_mapping          : dict من build_sf_mapping — competition_id → {sofascore_tournament_ids}
    """

    # ── Normalize dates ───────────────────────────────────────────────────────
    db_df = db_df.copy()
    sf_df = sf_df.copy()
    db_df["match_date"] = db_df["match_date"].apply(safe_date)
    sf_df["match_date"] = sf_df["match_date"].apply(safe_date)

    if tz_offset != 0:
        sf_df = apply_tz_offset(sf_df, tz_offset)

    if competition_filter:
        db_df = db_df[db_df["competition"].isin(competition_filter)].copy()
    if exclude_cancelled and "match_play_status" in db_df.columns:
        db_df = db_df[db_df["match_play_status"].str.lower() != "cancelled"].copy()

    # ── Build SF tournament ID whitelist from mapping ─────────────────────────
    # لو عندنا sf_mapping: نفلتر sf_df على البطولات المرتبطة ببطولاتنا فقط
    sf_allowed_ids = None
    if sf_mapping and "competition_id" in db_df.columns:
        sf_allowed_ids = set()
        for cid in db_df["competition_id"].dropna().astype(str).unique():
            sf_allowed_ids.update(sf_mapping.get(cid, set()))

    # Build competition_id → name map
    comp_id_to_name = {}
    if "competition_id" in db_df.columns and "competition" in db_df.columns:
        comp_id_to_name = dict(
            zip(db_df["competition_id"].astype(str), db_df["competition"])
        )

    results = []
    sf_matched = set()

    for _, db in db_df.iterrows():
        db_date = str(db.get("match_date", ""))
        db_home = str(db.get("home_team", ""))
        db_away = str(db.get("away_team", ""))
        db_comp = str(db.get("competition", ""))
        db_kick = str(db.get("kick_off_time", ""))[:5]

        db_home_n = normalize(db_home, mappings)
        db_away_n = normalize(db_away, mappings)
        db_comp_n = db_comp.lower().strip()

        # Candidate dates ±1 day
        try:
            d = datetime.strptime(db_date, "%Y-%m-%d")
            cand_dates = [(d + timedelta(days=x)).strftime("%Y-%m-%d") for x in (-1, 0, 1)]
        except Exception:
            cand_dates = [db_date]

        candidates = sf_df[sf_df["match_date"].isin(cand_dates)]
        best_score, best_idx = 0, None

        for idx, sf in candidates.iterrows():
            sf_home_n = normalize(str(sf["home_team"]), {})
            sf_away_n = normalize(str(sf["away_team"]), {})
            sf_tourn_n = str(sf.get("tournament", "")).lower().strip()

            # Teams score (weight 70%)
            teams_score = (fuzz.token_sort_ratio(db_home_n, sf_home_n) +
                           fuzz.token_sort_ratio(db_away_n, sf_away_n)) / 2

            # Competition boost (weight 30%) — helps avoid wrong tournament matches
            comp_score = fuzz.token_sort_ratio(db_comp_n, sf_tourn_n)

            # Combined: 70% teams + 30% competition
            score = teams_score * 0.70 + comp_score * 0.30

            if score > best_score:
                best_score, best_idx = score, idx

        if best_idx is not None and best_score >= fuzzy_threshold:
            sf = sf_df.loc[best_idx]
            sf_matched.add(best_idx)
            sf_kick = str(sf.get("kick_off_time", ""))[:5]

            time_diff = None
            try:
                if db_kick and sf_kick and len(db_kick) == 5 and len(sf_kick) == 5:
                    db_m = int(db_kick[:2]) * 60 + int(db_kick[3:])
                    sf_m = int(sf_kick[:2]) * 60 + int(sf_kick[3:])
                    time_diff = sf_m - db_m
            except Exception:
                pass

            status = "⏱ فرق كيك أوف" if (time_diff is not None and abs(time_diff) > 2) else "✓ متطابق"
            results.append({
                "status": status,
                "match_date": db_date,
                "competition": db_comp,
                "home_team": db_home,
                "away_team": db_away,
                "db_kickoff": db_kick,
                "sf_kickoff": sf_kick,
                "time_diff_min": time_diff,
                "match_score": round(best_score),
                "db_id": db.get("id", ""),
                "home_team_id": db.get("home_team_id", ""),
                "away_team_id": db.get("away_team_id", ""),
                "competition_id": db.get("competition_id", ""),
                "sf_home": sf["home_team"],
                "sf_away": sf["away_team"],
                "sf_tournament": sf.get("tournament", ""),
                "sf_category": sf.get("category", ""),
            })
        else:
            results.append({
                "status": "🟡 مش في SofaScore",
                "match_date": db_date,
                "competition": db_comp,
                "home_team": db_home,
                "away_team": db_away,
                "db_kickoff": db_kick,
                "sf_kickoff": "",
                "time_diff_min": None,
                "match_score": round(best_score),
                "db_id": db.get("id", ""),
                "home_team_id": db.get("home_team_id", ""),
                "away_team_id": db.get("away_team_id", ""),
                "competition_id": db.get("competition_id", ""),
                "sf_home": "",
                "sf_away": "",
                "sf_tournament": "",
                "sf_category": "",
            })

    # ── SofaScore matches not in DB ───────────────────────────────────────────
    db_name_to_id = {cname.lower().strip(): cid for cid, cname in comp_id_to_name.items()}
    selected_db_comp_names = list(comp_id_to_name.values())

    for idx, sf in sf_df.iterrows():
        if idx in sf_matched:
            continue

        sf_tourn    = str(sf.get("tournament", ""))
        sf_cat      = str(sf.get("category", ""))
        sf_tourn_id = str(sf.get("sofascore_id", ""))  # قد يكون فيه tournament_id

        # ── لو عندنا mapping دقيق: استخدمه ──────────────────────────────────
        if sf_allowed_ids is not None:
            # SofaScore data من الـ Tampermonkey script مش فيها tournament_id منفصل
            # بنعمل fuzzy match على اسم البطولة بس نقارن مع البطولات في الـ mapping فقط
            if not selected_db_comp_names:
                continue
            best = process.extractOne(
                sf_tourn.lower(),
                [c.lower() for c in selected_db_comp_names],
                scorer=fuzz.token_sort_ratio,
            )
            # لو عندنا mapping: threshold 85% عشان ماتشات دقيقة بس
            if not best or best[1] < 85:
                continue
        else:
            # ── Fallback: fuzzy بدون mapping ────────────────────────────────
            if not selected_db_comp_names:
                continue
            best = process.extractOne(
                sf_tourn.lower(),
                [c.lower() for c in selected_db_comp_names],
                scorer=fuzz.token_sort_ratio,
            )
            if not best or best[1] < 82:
                continue

        matched_db_name = selected_db_comp_names[
            [c.lower() for c in selected_db_comp_names].index(best[0])
        ]
        matched_comp_id = db_name_to_id.get(matched_db_name.lower().strip(), "")

        results.append({
            "status": "🔴 ناقص في DB",
            "match_date": str(sf["match_date"]),
            "competition": matched_db_name,
            "home_team": str(sf["home_team"]),
            "away_team": str(sf["away_team"]),
            "db_kickoff": "",
            "sf_kickoff": str(sf.get("kick_off_time", ""))[:5],
            "time_diff_min": None,
            "match_score": best[1],
            "db_id": "",
            "home_team_id": "",
            "away_team_id": "",
            "competition_id": matched_comp_id,
            "sf_home": str(sf["home_team"]),
            "sf_away": str(sf["away_team"]),
            "sf_tournament": sf_tourn,
            "sf_category": sf_cat,
        })

    return pd.DataFrame(results) if results else pd.DataFrame()


# ════════════════════════════════════════════════════════════════════════════════
# UI
# ════════════════════════════════════════════════════════════════════════════════
st.title("⚽ Match Data Comparator")
st.caption("قارن بياناتك مع SofaScore — اكتشف الماتشات الناقصة وفروق المواعيد")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ الإعدادات")

    today = datetime.now(tz=None).date()
    # Use UTC+3 (Cairo) for correct local date
    from datetime import timezone
    import zoneinfo
    try:
        cairo_tz = zoneinfo.ZoneInfo("Africa/Cairo")
        today = datetime.now(tz=cairo_tz).date()
    except Exception:
        # fallback: UTC+3
        today = (datetime.utcnow() + timedelta(hours=3)).date()
    st.subheader("📅 نطاق التاريخ")
    preset = st.radio("اختصارات", ["اليوم", "اليوم + بكرة", "أسبوع قادم", "مخصص"],
                      index=2, horizontal=True)
    if preset == "اليوم":
        date_from = date_to = today
    elif preset == "اليوم + بكرة":
        date_from, date_to = today, today + timedelta(days=1)
    elif preset == "أسبوع قادم":
        date_from, date_to = today, today + timedelta(days=7)
    else:
        c1, c2 = st.columns(2)
        date_from = c1.date_input("من", value=today)
        date_to = c2.date_input("إلى", value=today + timedelta(days=7))

    st.divider()
    st.subheader("🎯 حساسية المطابقة")
    fuzzy_threshold = st.slider("Fuzzy Match %", 50, 100, 78)

    st.divider()
    st.subheader("🕐 فارق التوقيت")
    tz_offset = st.number_input(
        "SofaScore UTC → توقيتك (ساعات)",
        min_value=-12, max_value=14, value=3, step=1,
        help="القاهرة = +3 | لو SofaScore بيكسب 3 ساعات عن DB بتاعك، اكتب 3"
    )
    st.caption(f"SofaScore 21:30 → عندك {(21 + tz_offset) % 24:02d}:30" if tz_offset else "مفيش تعديل على التوقيت")

    st.divider()
    st.subheader("🔧 خيارات")
    exclude_cancelled = st.checkbox("استبعاد Cancelled", value=True)

    st.divider()
    st.subheader("🗺️ Competition Mapping")
    st.caption("بيجيب الـ mapping من Google Sheet تلقائياً")

    mapping_df = pd.DataFrame()
    sf_mapping = {}

    if st.button("🔄 تحديث الـ Mapping", use_container_width=True):
        st.cache_data.clear()

    with st.spinner("جلب الـ mapping..."):
        mapping_df, mapping_err = load_mapping_from_sheet(SHEET_ID)

    if mapping_err:
        st.error(f"❌ {mapping_err}")
    elif not mapping_df.empty:
        sf_mapping = build_sf_mapping(mapping_df)
        mapped_count = sum(len(v) for v in sf_mapping.values())
        st.success(f"✅ {len(sf_mapping)} بطولة مربوطة ({mapped_count} tournament IDs)")
        with st.expander("🔍 Debug الـ mapping"):
            st.write("**الأعمدة الموجودة:**", mapping_df.columns.tolist())
            st.write("**أول 3 صفوف:**")
            st.dataframe(mapping_df.head(3))
            st.write("**sf_mapping sample:**", dict(list(sf_mapping.items())[:3]))
    else:
        st.warning("⚠️ الـ mapping فاضي")

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_main, tab_mapping, tab_help = st.tabs(["📊 المقارنة", "🔗 Name Mapping", "❓ مساعدة"])

with tab_mapping:
    st.subheader("جدول تعيين الأسماء")
    st.caption("لو اسم فريق في بياناتك مختلف عن اسمه على SofaScore")
    if "mappings" not in st.session_state:
        st.session_state.mappings = DEFAULT_MAPPINGS.copy()
    mdf = pd.DataFrame([{"DB Name": k, "SofaScore Name": v}
                        for k, v in st.session_state.mappings.items()])
    edited = st.data_editor(mdf, num_rows="dynamic", use_container_width=True)
    if st.button("💾 حفظ"):
        st.session_state.mappings = {
            r["DB Name"]: r["SofaScore Name"]
            for _, r in edited.iterrows() if r["DB Name"] and r["SofaScore Name"]
        }
        st.success(f"✅ تم حفظ {len(st.session_state.mappings)} mapping")

with tab_help:
    st.subheader("كيفية رفع CSV من SofaScore يدوياً")
    st.markdown("""
    لو الـ API اتبلوك، ممكن تجيب الداتا يدوياً بأي طريقة من دول:

    **طريقة 1 — SofaScore website مباشرة:**
    1. روح على sofascore.com
    2. اختار اليوم اللي عايزه
    3. في المتصفح: F12 → Network → ابحث عن `scheduled-events`
    4. افتح الـ request → Copy response
    5. احفظه كـ JSON وحوّله لـ CSV

    **طريقة 2 — استخدم أداة مجانية:**
    - [sofascore-api.vercel.app](https://sofascore-api.vercel.app) (غير رسمي)
    - أو أي SofaScore scraper على Apify (فيه free tier)

    **فورمات الـ CSV المطلوب (الأعمدة الأساسية):**
    ```
    home_team, away_team, match_date, kick_off_time, tournament
    Arsenal, Chelsea, 2026-09-25, 17:30, Premier League
    ```

    **الأعمدة البديلة المقبولة:**
    - home / home team / hometeam
    - away / away team / awayteam
    - date / game_date / event_date
    - time / kickoff / ko / start_time
    - league / competition / league_name
    """)

with tab_main:
    col1, col2, col3 = st.columns(3)

    with col1:
        st.subheader("🗄️ بطولاتك")
        comp_file = st.file_uploader(
            "competitions CSV",
            type=["csv"], key="comp",
            help="competition_id, competition, competition_country, season"
        )

    with col2:
        st.subheader("📋 ماتشاتك (DB)")
        match_file = st.file_uploader(
            "matches CSV من DBeaver",
            type=["csv"], key="matches",
            help="id, competition, home_team, away_team, match_date, kick_off_time"
        )

    with col3:
        st.subheader("🌐 ماتشات SofaScore")
        sf_file = st.file_uploader(
            "CSV من Tampermonkey script",
            type=["csv"], key="sf_manual",
            help="sofascore_id, home_team, away_team, tournament, match_date, kick_off_time"
        )

    # ── Load files ────────────────────────────────────────────────────────────
    comp_df = db_df = sf_manual_df = None

    if comp_file:
        try:
            comp_df = pd.read_csv(comp_file)
            comp_df.columns = [c.strip().lower() for c in comp_df.columns]
            with col1:
                st.success(f"✅ {len(comp_df)} بطولة")
        except Exception as e:
            with col1:
                st.error(f"خطأ: {e}")

    if match_file:
        try:
            db_df = pd.read_csv(match_file)
            db_df.columns = [c.strip().lower() for c in db_df.columns]
            db_df["match_date"] = pd.to_datetime(db_df["match_date"], errors="coerce").dt.strftime("%Y-%m-%d")
            with col2:
                st.success(f"✅ {len(db_df):,} ماتش")
        except Exception as e:
            with col2:
                st.error(f"خطأ: {e}")

    if sf_file:
        sf_manual_df, err = parse_sofascore_csv(sf_file)
        with col3:
            if err:
                st.error(f"❌ {err}")
            else:
                st.success(f"✅ {len(sf_manual_df):,} ماتش")
                with st.expander("معاينة SofaScore"):
                    st.dataframe(sf_manual_df.head(8), use_container_width=True)

    st.divider()

    if comp_df is not None and db_df is not None:

        st.subheader("🏆 اختار البطولات")
        st.caption("الأسماء دي من ملف الماتشات بتاعك مباشرة — مضمون التطابق 100%")

        # ── Always use DB match file for competition names (exact match guaranteed)
        db_comps_all = sorted(db_df["competition"].dropna().unique().tolist())

        # Optional: filter by country using the competitions file
        country_opts = []
        if comp_df is not None and "competition_country" in comp_df.columns:
            country_opts = sorted(comp_df["competition_country"].dropna().unique().tolist())

        c1, c2, c3 = st.columns([3, 1, 1])
        with c1:
            country_filter = st.multiselect(
                "فلتر بالدولة (اختياري)",
                options=country_opts,
                placeholder="كل الدول"
            )
        with c2:
            sel_all = st.button("✅ الكل")
        with c3:
            clr_all = st.button("❌ مسح")

        # Filter DB comps by country if selected
        if country_filter and comp_df is not None and "competition_country" in comp_df.columns:
            comps_in_country = comp_df[
                comp_df["competition_country"].isin(country_filter)
            ]["competition"].dropna().unique().tolist()
            # Match against actual DB comp names using fuzzy
            filtered_comps = []
            for db_comp in db_comps_all:
                best = process.extractOne(
                    db_comp.lower(),
                    [c.lower() for c in comps_in_country],
                    scorer=fuzz.token_sort_ratio,
                )
                if best and best[1] >= 70:
                    filtered_comps.append(db_comp)
        else:
            filtered_comps = db_comps_all

        if "selected_comps" not in st.session_state or sel_all:
            st.session_state.selected_comps = filtered_comps
        if clr_all:
            st.session_state.selected_comps = []

        selected_comps = st.multiselect(
            f"البطولات ({len(filtered_comps)} متاحة من ملف الماتشات)",
            options=filtered_comps,
            default=[c for c in st.session_state.selected_comps if c in filtered_comps],
            key="comp_sel",
        )
        st.session_state.selected_comps = selected_comps
        st.caption(f"محدد: {len(selected_comps)} من {len(db_comps_all)} بطولة")

        st.divider()

        days_n = (date_to - date_from).days + 1
        sf_ready = sf_manual_df is not None and not sf_manual_df.empty
        run_disabled = not selected_comps or not sf_ready

        c_run, c_info = st.columns([1, 3])
        with c_run:
            run_btn = st.button("▶ تشغيل المقارنة", type="primary",
                                disabled=run_disabled, use_container_width=True)
        with c_info:
            if not sf_ready:
                st.warning("⬆️ ارفع ملف SofaScore عشان تقدر تشغّل المقارنة")
            else:
                sf_count = len(sf_manual_df)
                st.info(f"📅 {date_from} ← {date_to} ({days_n} يوم) | 🏆 {len(selected_comps)} بطولة | 🌐 {sf_count:,} ماتش SofaScore")

        if run_btn:
            sf_df = sf_manual_df.copy()

            if sf_df.empty:
                st.warning("⚠️ مفيش بيانات SofaScore")
                st.stop()

            # ── Filter DB by date range ───────────────────────────────────────
            db_filtered = db_df[
                (db_df["match_date"] >= date_from.strftime("%Y-%m-%d")) &
                (db_df["match_date"] <= date_to.strftime("%Y-%m-%d"))
            ].copy()

            # ── DEBUG: show what we're working with ───────────────────────────
            with st.expander("🔍 Debug — إيه اللي بيتقارن بالظبط", expanded=True):
                d1, d2, d3 = st.columns(3)
                d1.metric("DB بعد فلتر التاريخ", len(db_filtered))
                d2.metric("SofaScore ماتشات", len(sf_df))
                d3.metric("البطولات المختارة", len(selected_comps))

                # DB comps vs selected
                if len(db_filtered) == 0:
                    st.error(f"⚠️ مفيش ماتشات في DB للفترة {date_from} ← {date_to}. تأكد من الـ date range في الـ sidebar.")
                    db_dates = db_df["match_date"].dropna().unique()
                    st.write(f"التواريخ الموجودة في DB: {sorted(db_dates)[:10]}")
                else:
                    db_comps_in_range = set(db_filtered["competition"].unique())
                    matched_comps = db_comps_in_range & set(selected_comps)
                    unmatched_comps = db_comps_in_range - set(selected_comps)

                    st.write(f"**بطولات DB في الفترة دي:** {len(db_comps_in_range)} بطولة")
                    st.write(f"**منها محددة في الفلتر:** {len(matched_comps)} ✅ | **غير محددة:** {len(unmatched_comps)} ⚠️")

                    if unmatched_comps:
                        st.warning(f"البطولات دي موجودة في DB بس مش محددة في الفلتر — مش هتتقارن:\n{sorted(unmatched_comps)}")

                    sf_dates = set(sf_df["match_date"].unique())
                    db_dates_set = set(db_filtered["match_date"].unique())
                    common_dates = sf_dates & db_dates_set
                    st.write(f"**تواريخ مشتركة بين DB و SofaScore:** {sorted(common_dates)}")

                    if not common_dates:
                        st.error("⚠️ مفيش تواريخ مشتركة! تأكد من الـ timezone offset.")
                        st.write(f"DB dates: {sorted(db_dates_set)} | SF dates: {sorted(sf_dates)}")

            with st.spinner("جاري المقارنة..."):
                # tracked_comp_ids = competition_id values من competitions_2026.csv
                tracked_comp_ids = None
                if comp_df is not None and "competition_id" in comp_df.columns:
                    tracked_comp_ids = set(
                        comp_df["competition_id"].dropna().astype(str).unique()
                    )

                result_df = compare(
                    db_filtered, sf_df, selected_comps,
                    fuzzy_threshold,
                    st.session_state.get("mappings", DEFAULT_MAPPINGS),
                    exclude_cancelled,
                    tz_offset=tz_offset,
                    tracked_comp_ids=tracked_comp_ids,
                    sf_mapping=sf_mapping,
                )

            if result_df.empty:
                st.warning("⚠️ النتيجة فاضية — تأكد من الـ debug فوق")
                st.stop()

            st.session_state.result_df = result_df
            st.session_state.sf_df = sf_df

        # ── Results ───────────────────────────────────────────────────────────
        if "result_df" in st.session_state and not st.session_state.result_df.empty:
            result_df = st.session_state.result_df
            counts = result_df["status"].value_counts()
            total  = len(result_df)

            n_miss  = counts.get("🔴 ناقص في DB", 0)
            n_extra = counts.get("🟡 مش في SofaScore", 0)
            n_time  = counts.get("⏱ فرق كيك أوف", 0)
            n_ok    = counts.get("✓ متطابق", 0)

            # DB-side totals (rows that came from DB)
            db_side  = n_extra + n_time + n_ok   # ماتشات DB اللي اتقارنت
            pct_ok   = round(n_ok / db_side * 100) if db_side else 0

            # ── Summary banner ────────────────────────────────────────────────
            st.markdown(f"""
<div style="background:#1e2535;border:1px solid #2a3147;border-radius:10px;padding:14px 20px;margin-bottom:12px">
<b style="color:#e2e8f0;font-size:15px">📊 ملخص المقارنة</b><br>
<span style="color:#8892a4;font-size:13px">
من الـ <b style="color:#e2e8f0">{db_side}</b> ماتش اللي جايين من DB:
&nbsp;✓ <b style="color:#22c55e">{n_ok} متطابق ({pct_ok}%)</b>
&nbsp;|&nbsp; ⏱ <b style="color:#a78bfa">{n_time} فرق وقت</b>
&nbsp;|&nbsp; 🟡 <b style="color:#f59e0b">{n_extra} مش في SofaScore</b>
&nbsp;&nbsp;&nbsp;&nbsp; + موجود على SofaScore بس مش في DB: <b style="color:#ef4444">{n_miss}</b>
</span>
</div>
""", unsafe_allow_html=True)

            # ── Metrics row ──────────────────────────────────────────────────
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("🔴 ناقص في DB",       n_miss,  help="موجود على SofaScore مش موجود عندك")
            c2.metric("🟡 مش في SofaScore",  n_extra, help="موجود عندك مش لاقيه على SofaScore")
            c3.metric("⏱ فرق كيك أوف",      n_time,  help="الماتش متطابق بس الوقت مختلف")
            c4.metric("✓ متطابق",            n_ok)
            c5.metric("دقة DB",              f"{pct_ok}%", delta=f"{n_ok}/{db_side}")

            st.progress(pct_ok / 100,
                        text=f"دقة مطابقة DB: {pct_ok}% — {n_ok} متطابق من {db_side} ماتش DB")

            st.divider()

            # ── Filters ──────────────────────────────────────────────────────
            f1, f2, f3, f4, f5 = st.columns([2, 2, 2, 1, 1])
            search   = f1.text_input("🔍 بحث", placeholder="اسم الفريق...")
            status_f = f2.multiselect(
                "الحالة",
                result_df["status"].unique().tolist(),
                default=result_df["status"].unique().tolist(),  # كل الحالات ظاهرة بالـ default
            )
            comp_f = f3.multiselect(
                "البطولة",
                sorted(result_df["competition"].unique().tolist()),
            )
            date_f = f4.selectbox(
                "التاريخ",
                ["الكل"] + sorted(result_df["match_date"].unique().tolist()),
            )
            min_score = f5.number_input("أدنى تطابق%", 0, 100, 0, step=5)

            disp = result_df.copy()
            if status_f:
                disp = disp[disp["status"].isin(status_f)]
            if comp_f:
                disp = disp[disp["competition"].isin(comp_f)]
            if date_f != "الكل":
                disp = disp[disp["match_date"] == date_f]
            if search:
                mask = (
                    disp["home_team"].str.contains(search, case=False, na=False) |
                    disp["away_team"].str.contains(search, case=False, na=False) |
                    disp["competition"].str.contains(search, case=False, na=False)
                )
                disp = disp[mask]
            if min_score > 0:
                disp = disp[disp["match_score"] >= min_score]

            st.caption(f"عرض **{len(disp):,}** من {total:,} نتيجة")

            # ── Table ─────────────────────────────────────────────────────────
            st.dataframe(
                disp[[
                    "status", "match_date", "competition",
                    "home_team", "away_team",
                    "db_kickoff", "sf_kickoff", "time_diff_min",
                    "match_score", "sf_tournament", "sf_category",
                    "db_id", "competition_id",
                ]].rename(columns={
                    "status":        "الحالة",
                    "match_date":    "التاريخ",
                    "competition":   "البطولة (DB)",
                    "home_team":     "الهوم",
                    "away_team":     "الأواي",
                    "db_kickoff":    "كيك أوف DB",
                    "sf_kickoff":    "كيك أوف SF",
                    "time_diff_min": "فرق (د)",
                    "match_score":   "تطابق%",
                    "sf_tournament": "بطولة SF",
                    "sf_category":   "دولة SF",
                    "db_id":         "DB ID",
                    "competition_id":"Comp ID",
                }),
                use_container_width=True,
                height=500,
                column_config={
                    "تطابق%": st.column_config.ProgressColumn(
                        "تطابق%", min_value=0, max_value=100, format="%d%%"
                    ),
                    "فرق (د)": st.column_config.NumberColumn("فرق (د)", format="%+d دقيقة"),
                },
            )

            # ── Export ────────────────────────────────────────────────────────
            st.divider()
            ec1, ec2, ec3 = st.columns(3)
            ec1.download_button(
                "⬇️ Export الفلتر الحالي",
                disp.to_csv(index=False).encode("utf-8-sig"),
                f"filtered_{date_from}_{date_to}.csv", "text/csv",
            )
            problems = result_df[result_df["status"] != "✓ متطابق"]
            ec2.download_button(
                "🚨 Export المشاكل فقط",
                problems.to_csv(index=False).encode("utf-8-sig"),
                f"problems_{date_from}_{date_to}.csv", "text/csv",
            )
            ec3.download_button(
                "✅ Export المتطابقات فقط",
                result_df[result_df["status"] == "✓ متطابق"].to_csv(index=False).encode("utf-8-sig"),
                f"matched_{date_from}_{date_to}.csv", "text/csv",
            )

            with st.expander("👁️ بيانات SofaScore الخام"):
                sf_s = st.text_input("بحث في SofaScore", key="sf_s")
                sf_disp = st.session_state.sf_df.copy()
                if sf_s:
                    sf_disp = sf_disp[
                        sf_disp["home_team"].str.contains(sf_s, case=False, na=False) |
                        sf_disp["away_team"].str.contains(sf_s, case=False, na=False) |
                        sf_disp["tournament"].str.contains(sf_s, case=False, na=False)
                    ]
                st.dataframe(sf_disp, use_container_width=True, height=300)

    elif comp_file is None or match_file is None:
        st.info("⬆️ ارفع ملف البطولات وملف الماتشات عشان تبدأ")
