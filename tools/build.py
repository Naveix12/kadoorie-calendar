#!/usr/bin/env python3
"""Builds the Kadoorie exams calendar site.

1. Downloads the public Google Calendar of every grade (or reads a snapshot file with --from-file).
2. Keeps exams, trips, days off and parents' days, and works out class, major and "partial" for each.
3. Writes _site/index.html (the page) and _site/ics/*.ics (one subscribable calendar per class / major).

Usage:  python3 tools/build.py [--from-file data.txt] [--out _site]
"""
import base64, datetime, hashlib, json, os, re, sys, urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
SITE_URL = "https://naveix12.github.io/kadoorie-calendar/"
TZ = ZoneInfo("Asia/Jerusalem")
YEAR_START, YEAR_END = "2026-09-01", "2027-08-31"
CAL_IDS = {"ז": "l87q337gt7le53puk0htj2nkjk", "ח": "itrnpu4mbr4ctgqe8qnu34s71c", "ט": "7rq4pp2mp03qj77d010ghh4i5c",
           "י": "kpdv330tlnviindduphjj4vsk8", "יא": "2pcccsk26qnj0hh7gpjpn3tti8", "יב": "ooqdca5vvr34cfst3rph1ecg3k"}
G = {"ז": 7, "ח": 8, "ט": 9, "י": 10, "יא": 11, "יב": 12}
GL = {v: k for k, v in G.items()}

# ---------------------------------------------------------------- 1. fetch + parse
def unesc(s):
    return s.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()

def parse_dt(prop, val):
    """returns (date 'YYYY-MM-DD', time 'HH:MM' or None)"""
    if "VALUE=DATE" in prop or re.fullmatch(r"\d{8}", val):
        return f"{val[0:4]}-{val[4:6]}-{val[6:8]}", None
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)", val)
    if not m:
        return None, None
    y, mo, d, h, mi, s, z = m.groups()
    dt = datetime.datetime(int(y), int(mo), int(d), int(h), int(mi))
    if z:
        dt = dt.replace(tzinfo=datetime.timezone.utc).astimezone(TZ)
    else:
        tzm = re.search(r"TZID=([^;:]+)", prop)
        if tzm:
            try:
                dt = dt.replace(tzinfo=ZoneInfo(tzm.group(1))).astimezone(TZ)
            except Exception:
                pass
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")

def parse_ics(text, grade):
    text = re.sub(r"\r?\n[ \t]", "", text)
    rows, ev = [], None
    for line in text.splitlines():
        if line == "BEGIN:VEVENT":
            ev = {}
        elif line == "END:VEVENT":
            if ev is not None:
                rows.append(ev)
            ev = None
        elif ev is not None and ":" in line:
            k, v = line.split(":", 1)
            name = k.split(";")[0]
            ev[name] = v
            ev[name + "__p"] = k
    out = []
    for e in rows:
        if "RRULE" in e or "DTSTART" not in e:
            continue
        d, t = parse_dt(e["DTSTART__p"], e["DTSTART"])
        if not d or d < YEAR_START or d > YEAR_END:
            continue
        end, tt = "", ""
        if "DTEND" in e:
            ed, et = parse_dt(e["DTEND__p"], e["DTEND"])
            if ed and not t:  # all-day: DTEND is exclusive
                last = (datetime.date.fromisoformat(ed) - datetime.timedelta(days=1)).isoformat()
                if last > d:
                    end = last
            if t and et:
                tt = f"{t}–{et}"
        elif t:
            tt = t
        if t and int(t[:2]) < 7:  # times before 7am in the school calendars are entry mistakes
            tt = ""
        s = unesc(e.get("SUMMARY", ""))
        s = re.sub(r"https?://\S+|\S+@\S+", "", s).replace("|", "/").strip()
        if re.search(r"שיחה עם", s):  # private meetings
            continue
        s = re.sub(r"((?:י|יא|יב)\d{1,2})\s*-\s*[א-ת]{2,8}$", r"\1", s)  # trailing teacher name
        s = re.sub(r",?\s*מורות מלוות:.*$", "", s)
        out.append((grade, d, end, tt, s))
    return out

def fetch_all():
    rows = []
    for g, cid in CAL_IDS.items():
        url = f"https://calendar.google.com/calendar/ical/kadoorie.org.il_{cid}%40group.calendar.google.com/public/basic.ics"
        req = urllib.request.Request(url, headers={"User-Agent": "kadoorie-calendar-site"})
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read().decode("utf-8")
        got = parse_ics(text, g)
        if not got:
            raise SystemExit(f"no events for grade {g} – stopping so the old site stays up")
        print(f"grade {g}: {len(got)} events")
        rows += got
    rows.sort(key=lambda r: (G[r[0]], r[1], r[4]))
    return rows

def read_file(path):
    rows = []
    for line in open(path, encoding="utf8"):
        if line.startswith("#") or not line.strip():
            continue
        g, d, e, t, s = line.rstrip("\n").split("|", 4)
        rows.append((g, d, e, t, s))
    return rows

# ---------------------------------------------------------------- 2. classify
SUBJ_RX = [
    ("ph", r"פיזיקה"), ("cs", r"מחשבים|מדעי המחשב|תכנות לאינטרנט"),
    ("me", r"מכטרוניקה|רובוטיקה|בקרה|מעמש|מיכון|הנדסה ומדע"), ("bi", r"ביולוגיה|בילוגיה"),
    ("co", r"תקשורת|הפקות"), ("th", r"תיאטרון|תאטרון"), ("ar", r"אמנות|אומנות"),
    ("ge", r"גאוגרפיה|גיאוגרפיה|ג\"ג"), ("en", r"מדעי הסביבה"), ("ch", r"כימיה"), ("tr", r"תחבורה"),
    ("au", r"אוטוטק"), ("ps", r"פסיכולוגיה"), ("so", r"מדעי החברה"), ("ab", r"ערבית"),
]
SUBJ = {"ph": "פיזיקה", "cs": "מחשבים", "me": "מכטרוניקה", "bi": "ביולוגיה", "co": "תקשורת והפקות", "th": "תיאטרון",
        "ar": "אמנות", "ge": "גאוגרפיה", "en": "מדעי הסביבה", "ch": "כימיה", "tr": "תחבורה", "au": "אוטוטק",
        "ps": "פסיכולוגיה", "so": "מדעי החברה", "ab": "ערבית"}
MAJORS = [("me", "מכטרוניקה", ["me"]), ("tr", "אוטוטק", ["tr", "au"]), ("cs", "מדעי המחשב ופיזיקה", ["cs", "ph"]),
          ("ad", "אמנויות העיצוב", ["ar"]), ("co", "תקשורת הפקות", ["co"]), ("bc", "ביולוגיה וכימיה", ["bi", "ch"]),
          ("be", "ביולוגיה ומדעי הסביבה", ["bi", "en"]), ("ba", "ביולוגיה וחקלאות", ["bi"]), ("ge", "גיאוגרפיה", ["ge"]),
          ("di", "דיפלומטיה", []), ("so", "מדעי החברה", ["so", "ps"]), ("ab", "ערבית", ["ab"]), ("th", "תיאטרון", ["th"])]
EXAM = r"מבחן|בוחן|מבדק|(?<![א-ת])בגרות|מתכונ|מתכנות|נבצרים|מועד ב|בחינה"
TRIP = r"טיול|גיחה|סיור|בעקבות לוחמים|בית לוחמי הגטאות|יציאה לתערוכת"
OFF = r"חופש|יום בחירות|לא לומדים|יום כיפור|גשר בין|חג השבועות|ראש השנה"
PAR = r"יום הורים|ימי הורים"
PARTIAL = r"מעבר|גוש|יחל|נבצרים|מועד ב|תנופה|עמ\"ט|חצי שכבה"

def classes(gl, s):
    gp = {"ז": "ז", "ח": "ח", "ט": "ט", "י": "י(?![אב])", "יא": "יא", "יב": "יב"}[gl]
    nums = []
    for m in re.finditer(rf"(?<![א-ת]){gp}\s?['׳]?\s?(\d{{1,2}})((?:\s*[+,]\s*(?:{gp}\s?['׳]?\s?)?\d{{1,2}}|\s+{gp}\s?['׳]?\s?\d{{1,2}})*)", s):
        nums.append(int(m.group(1)))
        nums += [int(x) for x in re.findall(r"\d{1,2}", m.group(2))]
    m = re.search(r"כיתות\s+(?:יב\s*)?(\d{1,2}(?:\s*,\s*\d{1,2})+)", s)
    if m:
        nums += [int(x) for x in re.findall(r"\d{1,2}", m.group(1))]
    return sorted(set(nums))

def typeword(o):
    for w, rx in [("בגרות פנימית", "בגרות פנימית"), ("בגרות", r"(?<![א-ת])בגרות"), ("מתכונת נבצרים", r"מתכונ\S* נבצרים"),
                  ("מתכונת 1", "מתכונת 1"), ("מתכונת 2", "מתכונת 2"), ("מתכונת", "מתכונ|מתכנות"),
                  ("מבחן מסכם", "מסכם|בחינה מסכמת"), ("בוחן", "בוחן"), ("מבחן", "")]:
        if re.search(rx, o):
            return w

def clean(o):
    s = o.replace("🏕️", "").replace("״", '"')
    if re.match(r"^(?:י\"?[אב]|[זחטי])['׳\"]?\s*[,\-–]\s", s):
        s = re.sub(r"^(?:י\"?[אב]|[זחטי])['׳\"]?\s*[,\-–]?\s*(?=[^\d])", "", s)
    s = re.sub(r"\s*(?:ל?שכב[הת]\s*(?:י\"?[אב]|[זחטי])['׳]?)\s*", " ", s)
    s = re.sub(r"\s*[-–]?\s*(?:ל?כיתו?ת\s*)?(?:(?:י\"?[אב]|יב|יא|[זחטי])\s?['׳]?\s?\d{1,2}(?:\s*[+,]\s*(?:(?:יב|יא|[זחטי])\s?['׳]?\s?)?\d{1,2}|\s+(?:יב|יא|[זחטי])\s?['׳]?\s?\d{1,2})*)", " ", s)
    s = re.sub(r"\s*(?:ל?כיתות|כיתה)\s+(?:יב\s*)?\d{1,2}(?:\s*,\s*\d{1,2})*", " ", s)
    s = re.sub(r"\s+[זחט]['׳](?=\s|$)", " ", s)
    s = re.sub(r"\s*\(?שעות 8-9\)?|\s8-9$", " 8:00–9:00", s)
    return re.sub(r"\s+", " ", s).strip(" -–,")

OVERRIDES = {
    "בוחן שכבתי ז- אנגלית": "בוחן אנגלית",
    "סיור ביד ושם": "סיור ביד ושם (יציאה 6:30, הגעה וחזרה עצמאית)",
    "טיול שנתי שכבת ח' - חאן הגליל בית העמק": "טיול שנתי – חאן הגליל, בית העמק",
    "יציאה ל\"בית לוחמי הגטאות\"": "בית לוחמי הגטאות (חצי שכבה בכל יום)",
    "יום בעקבות לוחמים בצפון": "יום בעקבות לוחמים בצפון",
    "טיול שנתי שכבת י'": "טיול שנתי",
    "טיול שנתי יב' חניון יעלים": "טיול שנתי – חניון יעלים",
    "גיחה שכבתית מעיין חרוד": "גיחה שכבתית – מעיין חרוד",
    "יציאה לתערוכת בוגרים": "תערוכת בוגרים ארצית באמנות",
    "צו ראשון לכיתת": "צו ראשון – אין לימודים",
    "מבחן מגמות:": "מבחן מגמה",
    "מתכונת נבצרים היסטוריה + ביולוגיה": "מתכונת נבצרים – היסטוריה / ביולוגיה",
}
SPLIT = {"מבחן במעבדה ביולוגיה ,מתכונת בג\"ג ,בגרות פנימית בקרה מורחב":
         [("bi", "מבחן מעבדה – ביולוגיה"), ("ge", "מתכונת – גאוגרפיה"), ("me", "בגרות פנימית – בקרה מורחב")]}

def fix(s):
    s = re.sub(r"\s+(?:ל?כיתה)$", "", s)
    s = s.replace("אנגלית-בוחן", "בוחן אנגלית").replace("סיור בילוגיה", "סיור ביולוגיה").replace("במעבדה בביולוגיה", "במעבדה ביולוגיה")
    s = s.replace("מבחן מתכנות", "מתכונת").replace("מבחן מתכונת", "מתכונת").replace("מתכנות", "מתכונת").replace("מבחן מתכונות", "מתכונת")
    s = re.sub(r"^מבחן במגמות\s*,?\s*(?=\S)", "מבחן מגמה – ", s)
    if s.strip() == "מבחן במגמות":
        s = "מבחן מגמות"
    s = s.replace("(בקרה מורחב )", "(בקרה מורחב)").replace("יחל'", 'יח"ל').replace(" יחל", ' יח"ל')
    s = s.replace("מבחן חלק א' לשון", "מבחן לשון – חלק א'").replace("מבחן חלק ב' לשון", "מבחן לשון – חלק ב'")
    s = s.replace("מבחן לכלל השכבה בתנ\"ך", "מבחן בתנ\"ך").replace("- גוש", " – גוש").replace("- שני הגושים", " – שני הגושים")
    s = s.replace("בוחן שכבתי – אנגלית", "בוחן אנגלית")
    return s.strip()

def classify(rows):
    events = []
    for gl, d, e, t, s in rows:
        g = G[gl]
        if re.search(PAR, s) or (re.search(OFF, s) and not re.search(r"חזרה ללימודים", s)):
            kind = "n"
        elif re.search(EXAM, s) and not re.search(r"ועדות|סיום לוח", s):
            kind = "x"
        elif re.search(TRIP, s):
            kind = "t"
        else:
            continue
        cl = classes(gl, s)
        if kind == "n":  # days off and parents' days come from NOTES; keep only class-specific ones
            if cl:
                events.append([g, d, e, t, "n", cl, [], 0, "צו ראשון – אין לימודים" if "צו" in s else clean(s), s, ""])
            continue
        m = []
        if g >= 10:
            m = [k for k, rx in SUBJ_RX if re.search(rx, s)]
            if not m and re.search(r"מגמות", s):
                m = ["*"]
        p = 1 if re.search(PARTIAL, s) or (g < 10 and re.search(r"עמ\"ט", s)) or ("רוסית" in s and not m) else 0
        if s in SPLIT:
            for mj, txt in SPLIT[s]:
                events.append([g, d, e, t, kind, cl, [mj], p, txt, s, ""])
            continue
        short = clean(s)
        for k, v in OVERRIDES.items():
            if k in s:
                short = v
        if "היסטוריה / ביולוגיה" in short:
            m, p = [], 1
        w = ("מבחן מגמה" if "מגמות" in s else typeword(s)) if len(m) > 1 else ""
        events.append([g, d, e, t, kind, cl, m, p, fix(short), s, w])
    events.sort(key=lambda r: (r[1], r[0]))
    return events

NOTES = [
    ["2026-09-11", "2026-09-13", "חופשת ראש השנה", [], ""],
    ["2026-09-20", "2026-09-21", "יום כיפור", [], ""],
    ["2026-09-22", "2026-09-24", "גשר – אין לימודים", [], ""],
    ["2026-09-25", "2026-10-03", "חופשת סוכות", [], ""],
    ["2026-10-27", "", "יום בחירות – אין לימודים", [], ""],
    ["2026-11-29", "", "יום הורים", [7, 10, 12], "יום הורים מצומצם"],
    ["2026-11-30", "", "יום הורים", [7, 10, 12], None],
    ["2026-12-06", "2026-12-12", "חופשת חנוכה", [], ""],
    ["2027-02-03", "", "יום הורים", [8, 9, 11], None],
    ["2027-02-04", "", "יום הורים", [11], None],
    ["2027-03-23", "2027-03-24", "חופשת פורים", [], ""],
    ["2027-04-13", "2027-04-28", "חופשת פסח", [], ""],
    ["2027-05-12", "", "יום העצמאות", [], ""],
    ["2027-06-10", "2027-06-12", "שבועות", [], ""],
    ["2027-06-13", "", "יום הורים מצומצם", [], ""],
]

# ---------------------------------------------------------------- 3. page
HEB = "U+0307-0308, U+0590-05FF, U+200C-2010, U+20AA, U+25CC, U+FB1D-FB4F"
LAT = "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD"

def font_css():
    F = os.path.join(HERE, "fonts")
    def face(fam, fn, w, rng):
        b = base64.b64encode(open(os.path.join(F, fn), "rb").read()).decode()
        return f"@font-face{{font-family:{fam};font-style:normal;font-weight:{w};font-display:swap;src:url(data:font/woff2;base64,{b}) format('woff2');unicode-range:{rng}}}"
    css = []
    for w in (400, 700):
        for sub, r in (("hebrew", HEB), ("latin", LAT)):
            css.append(face("KHeebo", f"heebo-{sub}-{w}-normal.woff2", w, r))
    for sub, r in (("hebrew", HEB), ("latin", LAT)):
        css.append(face("KSecular", f"secular-one-{sub}-400-normal.woff2", 400, r))
    for sub, r in (("hebrew", HEB), ("latin", LAT)):
        css.append(face("KHeebo", f"heebo-{sub}-700-normal.woff2", 800, r))
    return "\n".join(css)

# ---------------------------------------------------------------- 4. calendar feeds
def ics_esc(t):
    return t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

def fold(line):
    if len(line.encode()) <= 75:
        return line
    out, cur, n, lim = [], "", 0, 75
    for ch in line:
        l = len(ch.encode())
        if n + l > lim:
            out.append(cur); cur, n, lim = "", 0, 74
        cur += ch; n += l
    out.append(cur)
    return "\r\n ".join(out)

def feed(data, name, g, c=None, major=None):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    subs = set(dict((m[0], m[2]) for m in MAJORS).get(major, [])) if major else set()
    L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//kadoorie-calendar//HE", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
         "X-WR-CALNAME:" + ics_esc(name), "X-WR-TIMEZONE:Asia/Jerusalem", "REFRESH-INTERVAL;VALUE=DURATION:PT12H", "X-PUBLISHED-TTL:PT12H"]
    lab = GL[g] + (str(c) if c else "")
    def nd(d):
        return (datetime.date.fromisoformat(d) + datetime.timedelta(days=1)).strftime("%Y%m%d")
    def ev(d, e, title, desc):
        uid = hashlib.md5((d + title).encode()).hexdigest()[:12]
        L.extend(["BEGIN:VEVENT", f"UID:{d}-{uid}@kadoorie-calendar", "DTSTAMP:" + stamp, "DTSTART;VALUE=DATE:" + d.replace("-", ""),
                  "DTEND;VALUE=DATE:" + nd(e or d), "SUMMARY:" + ics_esc(title), "DESCRIPTION:" + ics_esc(desc), "TRANSP:TRANSPARENT", "END:VEVENT"])
    seen = set()
    for g2, d, e, t, k, cl, m, p, s, o, w in data["events"]:
        if g2 != g:
            continue
        suffix = ""
        if cl:
            if g < 10:
                if c not in cl:
                    continue
            else:
                suffix = " (רק ל" + ", ".join(GL[g] + str(x) for x in cl) + ")"
        if m:
            if not major:
                continue
            if m[0] != "*" and not any(x in subs for x in m):
                continue
        txt = s
        if len(m) > 1 and w:
            txt = w + " – " + ", ".join(SUBJ[x] for x in m if x in subs)
        if t and t.split("–")[0] not in txt:
            txt += " " + t
        title = lab + " · " + txt + suffix + (" (חלקי)" if p else "")
        if (d, title) in seen:
            continue
        seen.add((d, title))
        ev(d, e, title, "לוח מבחנים כדורי\nביומן בית הספר: " + o + ("\nרק לחלק מהכיתה" if p else ""))
    for d, e, txt, gs, alt in data["notes"]:
        tt = txt if (not gs or g in gs) else alt
        if tt:
            ev(d, e, "כדורי · " + tt, "לוח מבחנים כדורי")
    L.append("END:VCALENDAR")
    return "\r\n".join(fold(x) for x in L) + "\r\n"

# ---------------------------------------------------------------- main
def main():
    args = sys.argv[1:]
    out = args[args.index("--out") + 1] if "--out" in args else "_site"
    rows = read_file(args[args.index("--from-file") + 1]) if "--from-file" in args else fetch_all()
    asof = datetime.datetime.now(TZ).strftime("%-d.%-m.%Y")
    data = {"asOf": asof, "events": classify(rows), "notes": NOTES}
    os.makedirs(os.path.join(out, "ics"), exist_ok=True)
    with open(os.path.join(out, "data.txt"), "w", encoding="utf8") as f:
        f.write(f"# Kadoorie calendars, fetched {asof}\n" + "\n".join("|".join(r) for r in rows) + "\n")
    tpl = open(os.path.join(HERE, "site_template.html"), encoding="utf8").read()
    page = (tpl.replace("{{FONTCSS}}", font_css())
               .replace("{{DATA}}", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
               .replace("{{BASE}}", SITE_URL).replace("{{ASOF}}", asof))
    open(os.path.join(out, "index.html"), "w", encoding="utf8").write(page)
    open(os.path.join(out, ".nojekyll"), "w").write("")
    n = 0
    def write(fn, txt):
        nonlocal n
        open(os.path.join(out, "ics", fn), "w", encoding="utf8", newline="").write(txt); n += 1
    for g in (7, 8, 9):
        for c in range(1, 12):
            write(f"k{g}-{c}.ics", feed(data, f"כדורי · {GL[g]}{c}", g, c))
    for g in (10, 11, 12):
        write(f"k{g}.ics", feed(data, f"כדורי · שכבה {GL[g]}", g))
        for key, lab, _ in MAJORS:
            write(f"k{g}-{key}.ics", feed(data, f"כדורי · {GL[g]} · {lab}", g, None, key))
    print(f"source rows {len(rows)}, events {len(data['events'])}, feeds {n}, as of {asof}")

if __name__ == "__main__":
    main()
