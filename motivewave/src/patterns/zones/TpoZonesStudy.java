package patterns.zones;

import java.awt.BasicStroke;
import java.awt.Color;
import java.awt.Stroke;
import java.time.LocalDate;
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
import com.motivewave.platform.sdk.common.desc.IntegerDescriptor;
import com.motivewave.platform.sdk.common.desc.SettingGroup;
import com.motivewave.platform.sdk.common.desc.SettingTab;
import com.motivewave.platform.sdk.common.desc.SettingsDescriptor;
import com.motivewave.platform.sdk.draw.Box;
import com.motivewave.platform.sdk.draw.Figure;
import com.motivewave.platform.sdk.draw.Line;
import com.motivewave.platform.sdk.study.Study;
import com.motivewave.platform.sdk.study.StudyHeader;

import patterns.zones.ZoneEngine.Kind;
import patterns.zones.ZoneEngine.Params;
import patterns.zones.ZoneEngine.Result;

/**
 * TPO Zones 1.8.4 — плотная карта зон ES прямо на графике MotiveWave: обёртка SDK (настройки и фигуры).
 * Все пороги алгоритма зашиты в коде — значения по умолчанию ZoneEngine.Params (там же их описание); в настройках
 * индикатора только вид: цвета, заливка, что показывать, бары профиля. В jar классы лежат в пакете своей версии
 * (build.sh: patterns.zones → patterns.zones.v1_8_4): jar разных версий в расширениях MotiveWave не подменяют друг друга;
 * версия и сборка — в строке info индикатора. Расчёт — ZoneEngine, что рисовать — ZonePlan (оба без SDK); по 30m-барам
 * инструмента, на любом таймфрейме графика. Принцип действия — motivewave/README.md.
 */
@StudyHeader(
    namespace = "com.patterns",
    id = "TPO_ZONES_1_8_4",
    name = "TPO Zones 1.8.4",
    label = "TPO Zones",
    desc = "Плотная карта зон по RTH TPO-профилям: зоны проторговки и экстремумов на краях балансов, по телам свечей и квадратам TPO, цвет по композиту",
    menu = "Patterns",
    overlay = true,
    studyOverlay = false,
    requiresVolume = false,
    supportsBarUpdates = false)
public class TpoZonesStudy extends Study {
    /** Версия и сборка: build.sh подставляет в BUILD дату и коммит; в исходниках — "dev" (сборка не через build.sh). */
    static final String VERSION = "1.8.4";
    static final String BUILD = "dev";

    /** История, календарных дней (как грузит MotiveWave; на неё настроены пороги). */
    static final int HISTORY_DAYS = 1500;

    // ---- ключи настроек (только вид)
    static final String BAR30 = "bar30", SHOW_GREY = "showGrey", SHOW_EXT = "showExt", PREV = "prev",
            PREV_VA = "prevVaColor", PREV_POC = "prevPocColor", OP_MAIN = "opMain", OP_GREY = "opGrey";
    /** Виды зон, которые рисует индикатор, — подпись и цвет по умолчанию (прежние режимы 1.1–1.6 в настройки не выводятся). */
    static final Kind[] SHOWN = {Kind.CORE, Kind.HVN_R, Kind.HVN_S, Kind.GREY};
    static final Map<Kind, String> COLOR = new EnumMap<>(Kind.class), LABEL = new EnumMap<>(Kind.class);
    static final Map<Kind, Color> DEF_COLOR = new EnumMap<>(Kind.class);
    static {
        for (Kind k : Kind.values()) COLOR.put(k, "color_" + k.name());
        LABEL.put(Kind.CORE, "Синяя — проторговка композита"); LABEL.put(Kind.HVN_R, "Красная — сопротивление");
        LABEL.put(Kind.HVN_S, "Зелёная — поддержка"); LABEL.put(Kind.GREY, "Серая — минорная");
        DEF_COLOR.put(Kind.HVN_S, new Color(0x006400)); DEF_COLOR.put(Kind.HVN_R, new Color(0x8B0000));
        DEF_COLOR.put(Kind.HVN_RANGE, new Color(0x6250d6)); DEF_COLOR.put(Kind.HVN_NEW, new Color(0x7d858c));
        DEF_COLOR.put(Kind.OLD_BALANCE, new Color(0x00158B)); DEF_COLOR.put(Kind.GREY, new Color(0x7d858c));
        DEF_COLOR.put(Kind.CORE, new Color(0x00158B));
    }

    private LocalDate computedFor;   // сессия последнего бара на момент расчёта

    @Override
    public void initialize(Defaults defaults) {
        SettingsDescriptor sd = createSD();
        SettingTab tab = sd.addTab("Вид");
        SettingGroup g = tab.addGroup("Цвета зон");
        for (Kind k : SHOWN) g.addRow(new ColorDescriptor(COLOR.get(k), LABEL.get(k), DEF_COLOR.get(k)));
        g = tab.addGroup("Показывать");
        g.addRow(new BooleanDescriptor(SHOW_GREY, "Серые зоны", true));
        g.addRow(new BooleanDescriptor(SHOW_EXT, "Зоны экстремумов", true));
        g.addRow(new BooleanDescriptor(PREV, "Вчера: VAH / VAL / POC", true));
        g = tab.addGroup("Заливка, %");
        g.addRow(new IntegerDescriptor(OP_MAIN, "Зоны", 30, 0, 100, 1));
        g.addRow(new IntegerDescriptor(OP_GREY, "Серые", 30, 0, 100, 1));
        g = tab.addGroup("Вчерашний профиль");
        g.addRow(new ColorDescriptor(PREV_VA, "VAH / VAL", new Color(0x8a9097)));
        g.addRow(new ColorDescriptor(PREV_POC, "POC", new Color(0xe2662c)));
        g = tab.addGroup("Данные");
        g.addRow(new BarSizeDescriptor(BAR30, "Бары профиля (30 мин, не менять)", BarSize.minute(30)));
    }

    @Override
    public Long getMinStartTime(DataContext ctx) {
        return System.currentTimeMillis() - HISTORY_DAYS * 86_400_000L;
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
            info("TPO Zones " + VERSION + " (сборка " + BUILD + "): RTH-дней " + res.rthDays + " (полных " + res.fullDays + "), зон " + res.zones.size()
                    + ", из них зон экстремумов " + res.zones.stream().filter(z -> z.ext).count()
                    + ", не рисуется: узких (≤ 1 п.) " + res.thinDropped + ", лишних в стопках " + res.stackDropped
                    + (res.rawPrices ? ", роллов найдено " + res.rollMs.length + " (цены графика)" : ", роллов склеено " + res.rollMs.length)
                    + ", сессий на графике " + res.sessions.size() + (res.note.isEmpty() ? "" : " — " + res.note));
        } catch (Exception e) {
            error("TPO Zones " + VERSION + " (сборка " + BUILD + "): " + e);
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

    /** Параметры расчёта: значения по умолчанию ZoneEngine.Params + режим индикатора (по композиту, ядро ≤ 10 п.) и тик
     *  инструмента; из настроек — только какие зоны показывать. */
    private Params params(DataContext ctx) {
        Settings st = getSettings();
        Params p = new Params();
        p.compositeMode = true;
        p.zoneMaxWidthPts = 10;
        Instrument ins = ctx.getInstrument();
        p.tick = ins != null && ins.getTickSize() > 0 ? ins.getTickSize() : 0.25;
        if (!st.getBoolean(SHOW_GREY, true)) p.types.remove(Kind.GREY);
        p.extremes = st.getBoolean(SHOW_EXT, true);
        return p;
    }

    // ------------------------------------------------------------------ отрисовка
    private void draw(Result res, Params p) {
        Settings st = getSettings();
        ZonePlan plan = ZonePlan.of(res, p);
        Map<Kind, Color> fill = new EnumMap<>(Kind.class);
        for (Kind k : Kind.values()) {
            Color c = st.getColor(COLOR.get(k));
            if (c == null) c = DEF_COLOR.get(k);
            fill.put(k, alpha(c, k == Kind.GREY ? st.getInteger(OP_GREY, 30) : st.getInteger(OP_MAIN, 30)));
        }
        Color none = new Color(0, 0, 0, 0);
        List<Figure> figs = new ArrayList<>();
        for (ZonePlan.Rect r : plan.rects) {
            // куски разметки стоят вплотную: верх на 1 тик ниже, чтобы соседние полосы читались отдельно
            double hi = r.z.k == ZoneEngine.K_PART && r.hi - r.lo > 2 * p.tick ? r.hi - p.tick : r.hi;
            Box b = new Box(r.t0, hi, r.t1, r.lo);
            b.setFillColor(fill.get(r.kind));
            b.setLineColor(none);
            b.setUnderlay(true);
            b.setPopupMessage(r.tip);
            figs.add(b);
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
