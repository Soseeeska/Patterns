package patterns.zones;

import java.awt.BasicStroke;
import java.awt.Color;
import java.awt.Stroke;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.EnumMap;
import java.util.List;
import java.util.Map;

import com.motivewave.platform.sdk.common.BarSize;
import com.motivewave.platform.sdk.common.DataContext;
import com.motivewave.platform.sdk.common.DataSeries;
import com.motivewave.platform.sdk.common.Defaults;
import com.motivewave.platform.sdk.common.Instrument;
import com.motivewave.platform.sdk.common.Settings;
import com.motivewave.platform.sdk.common.desc.BarSizeDescriptor;
import com.motivewave.platform.sdk.common.desc.BooleanDescriptor;
import com.motivewave.platform.sdk.common.desc.ColorDescriptor;
import com.motivewave.platform.sdk.common.desc.DoubleDescriptor;
import com.motivewave.platform.sdk.common.desc.IntegerDescriptor;
import com.motivewave.platform.sdk.common.desc.SettingGroup;
import com.motivewave.platform.sdk.common.desc.SettingTab;
import com.motivewave.platform.sdk.common.desc.SettingsDescriptor;
import com.motivewave.platform.sdk.common.desc.StringDescriptor;
import com.motivewave.platform.sdk.draw.Box;
import com.motivewave.platform.sdk.draw.Figure;
import com.motivewave.platform.sdk.draw.Line;
import com.motivewave.platform.sdk.study.RuntimeDescriptor;
import com.motivewave.platform.sdk.study.Study;
import com.motivewave.platform.sdk.study.StudyHeader;

import patterns.zones.ZoneEngine.Kind;
import patterns.zones.ZoneEngine.Params;
import patterns.zones.ZoneEngine.Result;

/**
 * TPO Zones 1.1 — карта зон ES (ветка Zones-1.1) прямо на графике MotiveWave.
 * Расчёт — ZoneEngine (порт scripts/zone_map_export.py + фильтров страницы карты зон),
 * по 30m-барам инструмента; рисуется на любом таймфрейме графика.
 */
@StudyHeader(
    namespace = "com.patterns",
    id = "TPO_ZONES_1_1",
    name = "TPO Zones 1.1",
    label = "TPO Zones",
    desc = "Карта зон по RTH TPO-профилям: старые балансы, первородные HVN-зоны с классом по отторжениям, серые зоны",
    menu = "Patterns",
    overlay = true,
    studyOverlay = false,
    requiresVolume = false,
    supportsBarUpdates = false)
public class TpoZonesStudy extends Study {

    // ---- ключи настроек
    static final String BAR30 = "bar30", HISTORY_DAYS = "historyDays", TZ = "tz", RTH_START = "rthStart",
            RTH_END = "rthEnd", ETH_START = "ethStart", TICK = "tick", VA_PCT = "vaPct", HVN_SHARE = "hvnShare",
            HVN_MIN = "hvnMin", HVN_MAX = "hvnMax", GHVN_MIN = "greyHvnMin", GHVN_MAX = "greyHvnMax",
            MERGE_OV = "mergeOv", MERGE_W = "mergeW", COMP_DAYS = "compDays", GREY_HIST = "greyHist",
            GREY_WMIN = "greyWMin", GREY_WMAX = "greyWMax", REJ_SHARE = "rejShare", WICK = "wickTicks",
            SESSIONS = "sessions", NEAR = "near", ALIGN = "align", PROMOTE = "promote", MIN_REJ = "minRej",
            PUR_MIN = "purMin", PUR_MAX = "purMax", MIN_CONF = "minConf", CONF_NEAR = "confNear",
            MIN_AGE = "minAge", MAX_W = "maxW", EDGES = "edges", PREV = "prev", PREV_VA = "prevVaColor",
            PREV_POC = "prevPocColor", OP_MAIN = "opMain", OP_OLD = "opOld", OP_GREY = "opGrey", OP_EDGE = "opEdge";
    static final Map<Kind, String> SHOW = new EnumMap<>(Kind.class), COLOR = new EnumMap<>(Kind.class);
    static final Map<Kind, Color> DEF_COLOR = new EnumMap<>(Kind.class);
    static {
        SHOW.put(Kind.HVN_S, "showS"); SHOW.put(Kind.HVN_R, "showR"); SHOW.put(Kind.HVN_RANGE, "showRange");
        SHOW.put(Kind.HVN_NEW, "showNew"); SHOW.put(Kind.OLD_BALANCE, "showOld"); SHOW.put(Kind.GREY, "showGrey");
        for (Kind k : Kind.values()) COLOR.put(k, "color_" + k.name());
        DEF_COLOR.put(Kind.HVN_S, new Color(0x0f8a3c)); DEF_COLOR.put(Kind.HVN_R, new Color(0xd63b3a));
        DEF_COLOR.put(Kind.HVN_RANGE, new Color(0x6250d6)); DEF_COLOR.put(Kind.HVN_NEW, new Color(0x7d858c));
        DEF_COLOR.put(Kind.OLD_BALANCE, new Color(0x2a78d6)); DEF_COLOR.put(Kind.GREY, new Color(0x7d858c));
    }

    private LocalDate computedFor;   // сессия последнего бара на момент расчёта

    @Override
    public void initialize(Defaults defaults) {
        SettingsDescriptor sd = createSD();

        SettingTab tab = sd.addTab("Карта зон");
        SettingGroup g = tab.addGroup("Типы зон");
        g.addRow(new BooleanDescriptor(SHOW.get(Kind.HVN_S), Kind.HVN_S.title, true));
        g.addRow(new BooleanDescriptor(SHOW.get(Kind.HVN_R), Kind.HVN_R.title, true));
        g.addRow(new BooleanDescriptor(SHOW.get(Kind.HVN_RANGE), Kind.HVN_RANGE.title, true));
        g.addRow(new BooleanDescriptor(SHOW.get(Kind.HVN_NEW), Kind.HVN_NEW.title, true));
        g.addRow(new BooleanDescriptor(SHOW.get(Kind.OLD_BALANCE), Kind.OLD_BALANCE.title, false));
        g.addRow(new BooleanDescriptor(SHOW.get(Kind.GREY), Kind.GREY.title, true));
        g = tab.addGroup("Фильтры (как на странице карты зон)");
        g.addRow(new IntegerDescriptor(NEAR, "Ближайших к open сессии зон (0 — все)", 6, 0, 100, 1));
        g.addRow(new IntegerDescriptor(SESSIONS, "Сколько последних сессий показывать", 250, 5, 5000, 5));
        g.addRow(new IntegerDescriptor(ALIGN, "Подгонка границ по телам свечей, сессий (0 — без)", 0, 0, 200, 1));
        g.addRow(new IntegerDescriptor(PROMOTE, "Серая → основная: отторжений за подгонку (0 — не переводить)", 3, 0, 50, 1));
        g.addRow(new IntegerDescriptor(MIN_REJ, "Отторжений не меньше", 0, 0, 500, 1));
        g.addRow(new IntegerDescriptor(PUR_MIN, "Чистота отторжений от, %", 0, 0, 100, 5));
        g.addRow(new IntegerDescriptor(PUR_MAX, "Чистота отторжений до, % (100 — любая)", 100, 0, 100, 5));
        g.addRow(new IntegerDescriptor(MIN_CONF, "Подтверждений не меньше", 0, 0, 100, 1));
        g.addRow(new DoubleDescriptor(CONF_NEAR, "Подтверждение: HVN-полоса дня не дальше, п. (0 — пересекает зону)", 0, 0, 10, 0.25));
        g.addRow(new IntegerDescriptor(MIN_AGE, "Возраст зоны не меньше, дн.", 0, 0, 3000, 1));
        g.addRow(new DoubleDescriptor(MAX_W, "Ширина зоны до, п. (60 — любая)", 60, 1, 60, 0.5));

        tab = sd.addTab("Вид");
        g = tab.addGroup("Цвета зон");
        for (Kind k : Kind.values()) g.addRow(new ColorDescriptor(COLOR.get(k), k.title, DEF_COLOR.get(k)));
        g = tab.addGroup("Заливка, %");
        g.addRow(new IntegerDescriptor(OP_MAIN, "HVN-зоны", 24, 0, 100, 1));
        g.addRow(new IntegerDescriptor(OP_OLD, "Старые балансы", 10, 0, 100, 1));
        g.addRow(new IntegerDescriptor(OP_GREY, "Серые", 14, 0, 100, 1));
        g.addRow(new BooleanDescriptor(EDGES, "Линии границ", true));
        g.addRow(new IntegerDescriptor(OP_EDGE, "Непрозрачность границ, %", 85, 0, 100, 5));
        g = tab.addGroup("Вчерашний RTH-профиль");
        g.addRow(new BooleanDescriptor(PREV, "Показывать VAH / VAL / POC вчера", true));
        g.addRow(new ColorDescriptor(PREV_VA, "VAH / VAL", new Color(0x8a9097)));
        g.addRow(new ColorDescriptor(PREV_POC, "POC", new Color(0xe2662c)));

        tab = sd.addTab("Расчёт");
        g = tab.addGroup("Данные");
        g.addRow(new BarSizeDescriptor(BAR30, "Бары профиля (TPO-период)", BarSize.minute(30)));
        g.addRow(new IntegerDescriptor(HISTORY_DAYS, "Загружать историю, календарных дней", 1500, 60, 6000, 30));
        g.addRow(new StringDescriptor(TZ, "Часовой пояс сессий", "America/Chicago"));
        g.addRow(new StringDescriptor(RTH_START, "RTH начало (ЧЧ:ММ)", "08:30"));
        g.addRow(new StringDescriptor(RTH_END, "RTH конец (ЧЧ:ММ)", "15:00"));
        g.addRow(new StringDescriptor(ETH_START, "Начало Globex-сессии (ЧЧ:ММ)", "17:00"));
        g.addRow(new DoubleDescriptor(TICK, "Тик (0 — из инструмента)", 0, 0, 100, 0.01));
        g = tab.addGroup("Профиль и зоны (config.yaml)");
        g.addRow(new DoubleDescriptor(VA_PCT, "Value Area, доля", 0.70, 0.5, 0.95, 0.01));
        g.addRow(new DoubleDescriptor(HVN_SHARE, "HVN-полоса: TPO ≥ доли максимума", 0.70, 0.3, 1.0, 0.05));
        g.addRow(new IntegerDescriptor(HVN_MIN, "Первородные: HVN в VA от, TPO", 8, 1, 30, 1));
        g.addRow(new IntegerDescriptor(HVN_MAX, "Первородные: HVN в VA до, TPO", 10, 1, 30, 1));
        g.addRow(new IntegerDescriptor(GHVN_MIN, "Серые: HVN в VA от, TPO", 5, 1, 30, 1));
        g.addRow(new IntegerDescriptor(GHVN_MAX, "Серые: HVN в VA до, TPO", 7, 1, 30, 1));
        g.addRow(new DoubleDescriptor(MERGE_OV, "Пересечение для склейки, доля", 0.50, 0.05, 1.0, 0.05));
        g.addRow(new DoubleDescriptor(MERGE_W, "Ширина первородной зоны до, п.", 10.0, 1, 50, 0.5));
        g.addRow(new IntegerDescriptor(COMP_DAYS, "Композит «старый баланс» от, дней", 3, 2, 30, 1));
        g.addRow(new IntegerDescriptor(GREY_HIST, "Молодая первородная серая, пока истории < дней", 2, 0, 20, 1));
        g.addRow(new DoubleDescriptor(GREY_WMIN, "Серые: ширина от, п.", 1.0, 0, 50, 0.25));
        g.addRow(new DoubleDescriptor(GREY_WMAX, "Серые: ширина до, п.", 10.0, 0, 50, 0.25));
        g.addRow(new DoubleDescriptor(REJ_SHARE, "Класс HVN: доля отторжений в одну сторону", 0.60, 0.5, 1.0, 0.05));
        g.addRow(new IntegerDescriptor(WICK, "Хвост от, тиков", 2, 1, 20, 1));

        RuntimeDescriptor rd = createRD();
        rd.setLabelSettings(NEAR, ALIGN);
    }

    @Override
    public Long getMinStartTime(DataContext ctx) {
        int days = getSettings() == null ? 1500 : getSettings().getInteger(HISTORY_DAYS, 1500);
        return System.currentTimeMillis() - days * 86_400_000L;
    }

    // ------------------------------------------------------------------ расчёт
    @Override
    protected void calculateValues(DataContext ctx) {
        DataSeries s = series(ctx);
        computedFor = null;
        beginFigureUpdate();
        try {
            clearFigures();
            if (s == null || s.size() < 50) return;
            Params p = params(ctx);
            List<ZoneEngine.Bar> bars = new ArrayList<>(s.size());
            for (int i = 0; i < s.size(); i++) {
                float o = s.getOpen(i), h = s.getHigh(i), l = s.getLow(i), c = s.getClose(i);
                if (!(h > 0) || !(l > 0) || Float.isNaN(o) || Float.isNaN(c)) continue;
                bars.add(new ZoneEngine.Bar(s.getStartTime(i), o, h, l, c));
            }
            Result res = ZoneEngine.run(bars, p);
            if (res.sessions.isEmpty()) return;
            computedFor = res.sessions.get(res.sessions.size() - 1).date;
            draw(res, p);
            info("TPO Zones: RTH-дней " + res.rthDays + " (полных " + res.fullDays + "), зон " + res.zones.size()
                    + ", сессий на графике " + res.sessions.size() + (res.note.isEmpty() ? "" : " — " + res.note));
        } catch (Exception e) {
            error("TPO Zones: " + e);
        } finally {
            endFigureUpdate();
        }
    }

    @Override
    protected void calculate(int index, DataContext ctx) {
        // зоны меняются раз в сессию: пересчёт, когда на 30m началась новая сессия
        if (computedFor == null) return;
        DataSeries s = series(ctx);
        if (s == null || s.size() == 0) return;
        Params p = params(ctx);
        java.time.LocalDateTime lt = java.time.LocalDateTime.ofInstant(
                java.time.Instant.ofEpochMilli(s.getStartTime(s.size() - 1)), p.tz);
        LocalDate d = lt.toLocalTime().getHour() * 60 + lt.getMinute() >= p.ethStartMin ? lt.toLocalDate().plusDays(1) : lt.toLocalDate();
        if (!d.equals(computedFor)) calculateValues(ctx);
    }

    private DataSeries series(DataContext ctx) {
        BarSize bs = getSettings().getBarSize(BAR30);
        DataSeries s = bs == null ? null : ctx.getDataSeries(bs);
        if ((s == null || s.size() == 0)) {
            BarSize cb = ctx.getChartBarSize();
            if (cb != null && cb.getIntervalMinutes() == 30 && cb.isLinear()) s = ctx.getDataSeries();
        }
        return s;
    }

    private Params params(DataContext ctx) {
        Settings st = getSettings();
        Params p = new Params();
        Instrument ins = ctx.getInstrument();
        double tick = st.getDouble(TICK, 0);
        p.tick = tick > 0 ? tick : ins != null && ins.getTickSize() > 0 ? ins.getTickSize() : 0.25;
        try { p.tz = ZoneId.of(st.getString(TZ, "America/Chicago").trim()); } catch (Exception e) { p.tz = ZoneId.of("America/Chicago"); }
        p.rthStartMin = minutes(st.getString(RTH_START, "08:30"), 8 * 60 + 30);
        p.rthEndMin = minutes(st.getString(RTH_END, "15:00"), 15 * 60);
        p.ethStartMin = minutes(st.getString(ETH_START, "17:00"), 17 * 60);
        p.fullDayPeriods = Math.max(1, (p.rthEndMin - p.rthStartMin) / p.periodMinutes);
        p.valueAreaPct = st.getDouble(VA_PCT, 0.70);
        p.hvnShare = st.getDouble(HVN_SHARE, 0.70);
        p.hvnMinTpo = st.getInteger(HVN_MIN, 8); p.hvnMaxTpo = st.getInteger(HVN_MAX, 10);
        p.greyHvnMinTpo = st.getInteger(GHVN_MIN, 5); p.greyHvnMaxTpo = st.getInteger(GHVN_MAX, 7);
        p.mergeOverlapMin = st.getDouble(MERGE_OV, 0.5); p.mergeMaxWidthPts = st.getDouble(MERGE_W, 10.0);
        p.compositeMinDays = st.getInteger(COMP_DAYS, 3);
        p.greyCompositeMinDays = Math.min(2, p.compositeMinDays);
        p.greyMinHistoryDays = st.getInteger(GREY_HIST, 2);
        p.greyMinWidthPts = st.getDouble(GREY_WMIN, 1.0); p.greyMaxWidthPts = st.getDouble(GREY_WMAX, 10.0);
        p.rejectionShare = st.getDouble(REJ_SHARE, 0.6);
        p.minWickTicks = st.getInteger(WICK, 2);
        p.sessions = st.getInteger(SESSIONS, 250);
        p.nearest = st.getInteger(NEAR, 6);
        p.alignSessions = st.getInteger(ALIGN, 0);
        p.promoteRejections = st.getInteger(PROMOTE, 3);
        p.minRejections = st.getInteger(MIN_REJ, 0);
        p.purityMinPct = st.getInteger(PUR_MIN, 0); p.purityMaxPct = st.getInteger(PUR_MAX, 100);
        p.minConfirmations = st.getInteger(MIN_CONF, 0);
        p.confirmNearPts = st.getDouble(CONF_NEAR, 0);
        p.minAgeDays = st.getInteger(MIN_AGE, 0);
        p.maxWidthPts = st.getDouble(MAX_W, 60);
        p.types.clear();
        for (Kind k : Kind.values()) if (st.getBoolean(SHOW.get(k), k != Kind.OLD_BALANCE)) p.types.add(k);
        return p;
    }

    private static int minutes(String hhmm, int def) {
        try { LocalTime t = LocalTime.parse(hhmm.trim()); return t.getHour() * 60 + t.getMinute(); }
        catch (Exception e) { return def; }
    }

    // ------------------------------------------------------------------ отрисовка
    private void draw(Result res, Params p) {
        Settings st = getSettings();
        ZonePlan plan = ZonePlan.of(res, p);
        boolean edges = st.getBoolean(EDGES, true);
        int edgeA = st.getInteger(OP_EDGE, 85);
        Map<Kind, Color> fill = new EnumMap<>(Kind.class), line = new EnumMap<>(Kind.class);
        for (Kind k : Kind.values()) {
            Color c = st.getColor(COLOR.get(k));
            if (c == null) c = DEF_COLOR.get(k);
            int op = k == Kind.OLD_BALANCE ? st.getInteger(OP_OLD, 10) : k == Kind.GREY ? st.getInteger(OP_GREY, 14) : st.getInteger(OP_MAIN, 24);
            fill.put(k, alpha(c, op));
            line.put(k, alpha(c, edgeA));
        }
        Stroke solid = new BasicStroke(1f);
        Color none = new Color(0, 0, 0, 0);
        List<Figure> figs = new ArrayList<>();
        for (ZonePlan.Rect r : plan.rects) {
            Box b = new Box(r.t0, r.z.bhi, r.t1, r.z.blo);
            b.setFillColor(fill.get(r.kind));
            b.setLineColor(none);
            b.setUnderlay(true);
            b.setPopupMessage(r.tip);
            figs.add(b);
            if (!edges) continue;
            for (double y : new double[]{r.z.bhi, r.z.blo}) {
                Line ln = new Line(r.t0, y, r.t1, y);
                ln.setColor(line.get(r.kind)); ln.setStroke(solid); ln.setUnderlay(true);
                ln.setPopupMessage(r.tip);
                figs.add(ln);
            }
        }
        if (st.getBoolean(PREV, true)) {
            Color va = st.getColor(PREV_VA), poc = st.getColor(PREV_POC);
            if (va == null) va = new Color(0x8a9097);
            if (poc == null) poc = new Color(0xe2662c);
            Stroke dash = new BasicStroke(1f, BasicStroke.CAP_BUTT, BasicStroke.JOIN_MITER, 10f, new float[]{5f, 4f}, 0f);
            Stroke pocS = new BasicStroke(1.6f);
            for (ZonePlan.Level l : plan.levels) {
                Line ln = new Line(l.t0, l.price, l.t1, l.price);
                ln.setColor(l.poc ? poc : va); ln.setStroke(l.poc ? pocS : dash); ln.setPopupMessage(l.tip);
                figs.add(ln);
            }
        }
        addFigures(figs);
    }

    private static Color alpha(Color c, int pct) {
        return new Color(c.getRed(), c.getGreen(), c.getBlue(), Math.max(0, Math.min(255, Math.round(pct * 2.55f))));
    }

}
