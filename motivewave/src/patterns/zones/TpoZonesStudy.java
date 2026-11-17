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
 * TPO Zones 1.8 — плотная карта зон ES прямо на графике MotiveWave: обёртка SDK (настройки и фигуры).
 * Расчёт — ZoneEngine, что рисовать — ZonePlan (оба без SDK); по 30m-барам инструмента, на любом таймфрейме графика.
 * Принцип действия — motivewave/README.md.
 */
@StudyHeader(
    namespace = "com.patterns",
    id = "TPO_ZONES_1_8",
    name = "TPO Zones 1.8",
    label = "TPO Zones",
    desc = "Плотная карта зон по RTH TPO-профилям: сессионные зоны по телам свечей и квадратам TPO, цвет по композиту, серые — минорные, узкие, колеблющиеся",
    menu = "Patterns",
    overlay = true,
    studyOverlay = false,
    requiresVolume = false,
    supportsBarUpdates = false)
public class TpoZonesStudy extends Study {

    // ---- ключи настроек
    static final String S_MAX = "sessMax", MINOR = "sessMinor", CORE_TOUCH = "sessCoreTouch", CORE_NEAR = "coreNearestBlue", REFINE = "sessRefine", VA_PREV = "compVaPrevDay", RESYNC = "sessResyncLater", ROLL_BRK = "compRollBreak", ONE_COLOR = "drawOneColor", EXT_DAYS = "drawExtendDays", LIVE = "liveComposite", ROLL = "rollAdjust", ROLL_RAW = "rollRaw", ROLL_GAP = "rollMinGap", FALSE_BRK = "compFalseBreak", COMP_VA = "compVaOverlap", COMP_AND = "compJoinBoth", COMP_IN = "compInsideShare", COMP_OUT = "compOutsideDays", S_GAP = "sessMinGap", S_SHARE = "sessPeakShare", SESS = "sessionZones", S_PEAK = "sessMinPeak", S_BODY = "sessBody", CLUST = "bodyClusters", CL_DIP = "clusterDip", CL_MAX = "clusterMax", SUB_ADD = "subAddsZones", FIT_ETH = "fitEth", FIT_B = "fitBody", FIT_C = "fitCluster", PMAXB = "partMaxBody", PART = "partition", BODY = "bodyShare", PCORE = "partCore", PTGT = "partTarget", NODES = "coreNodes", NODE_DIP = "nodeDip", NODE_DROP = "nodeDrop", NO_OVERLAP = "noOverlap", CHART_NO_OV = "chartNoOverlap", GRID = "gridPts", GRID_OFF = "gridOffset", GRID_GREY = "gridNarrowGrey", GRID_BLUE = "gridNarrowKeepBlue", ONE_SQ = "oneSquareGrey", ONE_SQ_MAX = "oneSquareMax", CR = "compRejGrey", CR_RATIO = "compRejRatio", CR_MIN = "compRejMin", INV = "invertGrey", INV_GAP = "invertGap", MIN_PIECE = "minPiece", SHORT_DAYS = "compShortDays", SUB_OV = "subOverlap", SUB_RATIO = "subRatio", CLIFF = "coreCliff", CORE_DAYS = "coreMinDays", CORE_SESS = "coreFromSessions", JOIN_GAP = "joinGap", ZONE_W = "zoneMaxW", MODE = "compositeMode", CORE_VA = "coreByVa", BAR30 = "bar30", HISTORY_DAYS = "historyDays", TZ = "tz", RTH_START = "rthStart",
            RTH_END = "rthEnd", ETH_START = "ethStart", TICK = "tick", VA_PCT = "vaPct", HVN_SHARE = "hvnShare",
            HVN_MIN = "hvnMin", HVN_MAX = "hvnMax", GHVN_MIN = "greyHvnMin", GHVN_MAX = "greyHvnMax",
            MERGE_OV = "mergeOv", MERGE_W = "mergeW", COMP_DAYS = "compDays", GREY_HIST = "greyHist",
            GREY_WMIN = "greyWMin", GREY_WMAX = "greyWMax", REJ_SHARE = "rejShare", WICK = "wickTicks",
            SESSIONS = "sessions", NEAR = "near", ALIGN = "align", PROMOTE = "promote", MINOR_PROMOTE = "sessMinorPromote", MIN_REJ = "minRej",
            PUR_MIN = "purMin", PUR_MAX = "purMax", MIN_CONF = "minConf", CONF_NEAR = "confNear",
            MIN_AGE = "minAge", MAX_W = "maxW", EDGES = "edges", PREV = "prev", PREV_VA = "prevVaColor",
            PREV_POC = "prevPocColor", OP_MAIN = "opMain", OP_OLD = "opOld", OP_GREY = "opGrey", OP_EDGE = "opEdge";
    static final Map<Kind, String> SHOW = new EnumMap<>(Kind.class), COLOR = new EnumMap<>(Kind.class);
    static final Map<Kind, Color> DEF_COLOR = new EnumMap<>(Kind.class);
    static {
        SHOW.put(Kind.HVN_S, "showS"); SHOW.put(Kind.HVN_R, "showR"); SHOW.put(Kind.HVN_RANGE, "showRange");
        SHOW.put(Kind.HVN_NEW, "showNew"); SHOW.put(Kind.OLD_BALANCE, "showOld"); SHOW.put(Kind.GREY, "showGrey");
        SHOW.put(Kind.CORE, "showCore");
        for (Kind k : Kind.values()) COLOR.put(k, "color_" + k.name());
        DEF_COLOR.put(Kind.HVN_S, new Color(0x006400)); DEF_COLOR.put(Kind.HVN_R, new Color(0x8B0000));
        DEF_COLOR.put(Kind.HVN_RANGE, new Color(0x6250d6)); DEF_COLOR.put(Kind.HVN_NEW, new Color(0x7d858c));
        DEF_COLOR.put(Kind.OLD_BALANCE, new Color(0x00158B)); DEF_COLOR.put(Kind.GREY, new Color(0x7d858c));
        DEF_COLOR.put(Kind.CORE, new Color(0x00158B));
    }

    private LocalDate computedFor;   // сессия последнего бара на момент расчёта

    @Override
    public void initialize(Defaults defaults) {
        SettingsDescriptor sd = createSD();

        // Вкладки по шагам алгоритма (принцип действия — motivewave/README.md). Ключи настроек не менялись: сохранённые
        // в MotiveWave значения подхватываются.
        SettingTab tab = sd.addTab("Зоны");
        SettingGroup g = tab.addGroup("Композиты");
        g.addRow(new IntegerDescriptor(COMP_DAYS, "Композит от, дней", 3, 2, 30, 1));
        g.addRow(new BooleanDescriptor(COMP_AND, "День входит, только если close в диапазоне И наслоение VA (выкл. — ИЛИ)", true));
        g.addRow(new DoubleDescriptor(COMP_VA, "Наслоение VA — VA дня и композита пересекаются на ≥ долю меньшей (0 — не проверять)", 0.5, 0, 1, 0.05));
        g.addRow(new BooleanDescriptor(VA_PREV, "Наслоение VA — с VA композита или с VA предыдущего дня (value переходит изо дня в день)", true));
        g.addRow(new DoubleDescriptor(COMP_IN, "Внутренний день (High–Low на ≥ долю внутри диапазона композита) входит по ИЛИ (0 — выкл.)", 0.8, 0, 1, 0.05));
        g.addRow(new BooleanDescriptor(COMP_OUT, "Внешний день (диапазон композита на ≥ ту же долю внутри дня) — тоже по ИЛИ", true));
        g.addRow(new BooleanDescriptor(FALSE_BRK, "Ложный выход: возврат close в диапазон закрытого композита доклеивает дни к нему", true));
        g.addRow(new BooleanDescriptor(SHORT_DAYS, "Короткие дни (ранние закрытия) входят в композиты", true));
        g.addRow(new BooleanDescriptor(ROLL_BRK, "Ролл разрывает композит", true));
        g.addRow(new BooleanDescriptor(CORE_SESS, "Ядро — от POC до обрыва TPO; баланс — если ядро задевают VA его дней", true));
        g.addRow(new IntegerDescriptor(CLIFF, "Обрыв TPO: строка на N принтов ниже максимума предыдущего 1 п. — граница ядра (0 — выкл.)", 4, 0, 50, 1));
        g.addRow(new DoubleDescriptor(CORE_DAYS, "Композит — баланс, если ядро задевают VA ≥ доли его дней (0 — любой)", 0.5, 0, 1, 0.05));
        g.addRow(new DoubleDescriptor(ZONE_W, "Ядро композита не шире, п. (сужается до окна с POC; 0 — без лимита)", 10.0, 0, 60, 0.5));
        g.addRow(new DoubleDescriptor(BODY, "Тело композита — строки не ниже доли максимума TPO (зона в теле получает роль от композита)", 0.15, 0.02, 0.6, 0.01));
        g.addRow(new DoubleDescriptor(PMAXB, "Тело композита не шире, п. (шире — остаётся плотная середина)", 100, 20, 400, 5));
        g.addRow(new DoubleDescriptor(PCORE, "Ядро тела — строки не ниже доли максимума TPO (если синяя — не только у ядра)", 0.65, 0.3, 0.95, 0.05));
        g.addRow(new DoubleDescriptor(SUB_OV, "Уточнение: ≥ доли диапазона композита внутри раннего баланса — своего баланса не даёт (0 — выкл.)", 0.70, 0, 1, 0.05));
        g.addRow(new DoubleDescriptor(SUB_RATIO, "Уточнение: если тот баланс не шире композита больше чем в, раз", 2.0, 1, 10, 0.1));
        g.addRow(new BooleanDescriptor(LIVE, "Формирующийся композит: с 3-го полного дня задаёт роли; пока формируется — пересинхронизация", true));
        g.addRow(new BooleanDescriptor(RESYNC, "Старую зону синхронизирует и более поздний композит, сложившийся на ней (выкл. — только первый)", false));

        g = tab.addGroup("Сессионные зоны");
        g.addRow(new BooleanDescriptor(SESS, "Зоны из сессий: узлы RTH TPO дня, подогнанные по телам свечей; композит задаёт роль", true));
        g.addRow(new DoubleDescriptor(S_SHARE, "Узел — вершина не ниже доли главного узла дня", 0.25, 0, 1, 0.05));
        g.addRow(new IntegerDescriptor(S_PEAK, "Вершина узла от, TPO", 3, 1, 13, 1));
        g.addRow(new BooleanDescriptor(REFINE, "Уточнение: внутри узла пошире — все скопления тел свечей, каждое — зона (выкл. — одно скопление)", true));
        g.addRow(new DoubleDescriptor(S_BODY, "Подгонка: скопление — тела не ниже доли максимума тел", 0.5, 0.1, 0.95, 0.05));
        g.addRow(new DoubleDescriptor(S_MAX, "Зона не шире, п.", 4.0, 1, 20, 0.5));
        g.addRow(new DoubleDescriptor(GRID, "Квадрат TPO на графике, п.: края зон — по целым квадратам, квадрат с принтами не режется (0 — выкл.)", 1.0, 0, 10, 0.25));
        g.addRow(new DoubleDescriptor(GRID_OFF, "Сетка квадратов TPO: сдвиг от целых, п.", 0, 0, 5, 0.25));
        g.addRow(new DoubleDescriptor(S_GAP, "Узел ближе, п., к существующей зоне — её подтверждение (зазор между зонами)", 0.5, 0, 20, 0.25));
        g.addRow(new BooleanDescriptor(MINOR, "Минорные серые зоны: слабый узел сессии там, где нет других зон", true));
        g.addRow(new BooleanDescriptor(MINOR_PROMOTE, "Сильный узел на месте минорной зоны продолжает её обычной зоной (выкл. — вытесняет, зона обрывается)", true));

        g = tab.addGroup("Цвет и серые");
        g.addRow(new BooleanDescriptor(CORE_TOUCH, "Синяя — только зона, задевающая ядро композита (выкл. — всё ядро тела)", true));
        g.addRow(new BooleanDescriptor(CORE_NEAR, "Ядро не задевает ни одна зона композита — синей становится ближайшая к ядру", true));
        g.addRow(new BooleanDescriptor(GRID_GREY, "Скопление тел уже одного квадрата TPO — зона в один квадрат, серая", true));
        g.addRow(new BooleanDescriptor(ONE_SQ, "Узкая зона — всегда серая", true));
        g.addRow(new DoubleDescriptor(ONE_SQ_MAX, "... узкая — не шире, п.", 2.0, 0.25, 10, 0.25));
        g.addRow(new BooleanDescriptor(GRID_BLUE, "Узкие серые — кроме синих: узкая зона у ядра композита остаётся синей", true));
        g.addRow(new BooleanDescriptor(CR, "Красная / зелёная, колеблющаяся в своём композите, — серая", true));
        g.addRow(new DoubleDescriptor(CR_RATIO, "... перевес отторжений за цвет в днях композита не меньше, раз", 2.0, 1, 10, 0.25));
        g.addRow(new IntegerDescriptor(CR_MIN, "... проверять, если отторжений в композите от", 2, 1, 50, 1));
        g.addRow(new BooleanDescriptor(INV, "Стык «зелёные над красными»: две зоны стыка — серые", true));
        g.addRow(new DoubleDescriptor(INV_GAP, "... щель стыка не больше, п.", 3.0, 0, 20, 0.5));
        g.addRow(new IntegerDescriptor(WICK, "Хвост (отторжение) от, тиков", 2, 1, 20, 1));

        g = tab.addGroup("На графике");
        g.addRow(new BooleanDescriptor(NO_OVERLAP, "Зоны в сессии не налезают друг на друга (младшая не рисуется)", true));
        g.addRow(new BooleanDescriptor(CHART_NO_OV, "Одна зона на ценовом уровне графика: младшая налезающая сужается до свободного куска или не рисуется", true));
        g.addRow(new DoubleDescriptor(MIN_PIECE, "Суженная зона уже, п. — не рисуется", 1.0, 0.25, 10, 0.25));
        g.addRow(new BooleanDescriptor(ONE_COLOR, "Зона одной полосой: цвет и границы на сегодня по всей длине (выкл. — по участкам, как менялась)", true));
        g.addRow(new IntegerDescriptor(EXT_DAYS, "Зоны вправо за последнюю свечу, дней (0 — до конца сессии)", 7, 0, 365, 1));
        g.addRow(new IntegerDescriptor(SESSIONS, "Сколько последних сессий показывать", 250, 5, 5000, 5));

        g = tab.addGroup("Типы зон");
        g.addRow(new BooleanDescriptor(SHOW.get(Kind.HVN_S), Kind.HVN_S.title, true));
        g.addRow(new BooleanDescriptor(SHOW.get(Kind.HVN_R), Kind.HVN_R.title, true));
        g.addRow(new BooleanDescriptor(SHOW.get(Kind.HVN_RANGE), Kind.HVN_RANGE.title, true));
        g.addRow(new BooleanDescriptor(SHOW.get(Kind.HVN_NEW), Kind.HVN_NEW.title, true));
        g.addRow(new BooleanDescriptor(SHOW.get(Kind.OLD_BALANCE), Kind.OLD_BALANCE.title, false));
        g.addRow(new BooleanDescriptor(SHOW.get(Kind.GREY), Kind.GREY.title, true));
        g.addRow(new BooleanDescriptor(SHOW.get(Kind.CORE), Kind.CORE.title + " (режим «по композиту»)", true));

        tab = sd.addTab("Прежние режимы");
        g = tab.addGroup("Класс и разметка (1.1–1.6)");
        g.addRow(new BooleanDescriptor(MODE, "По положению в композите: выше ядра — сопротивление, ниже — поддержка (выкл. — по отторжениям, как на странице, 1.1)", true));
        g.addRow(new BooleanDescriptor(CORE_VA, "Ядро композита — VA (иначе HVN-полоса вокруг POC)", false));
        g.addRow(new BooleanDescriptor(PART, "Разметка композита (1.6, без сессионных зон): тело целиком режется на зоны", true));
        g.addRow(new DoubleDescriptor(FIT_B, "Разметка: подгонка по телам — меньше доли максимума тел свечей — пустота (0 — без подгонки)", 0.20, 0, 0.8, 0.05));
        g.addRow(new DoubleDescriptor(FIT_C, "Разметка: зона — тела не ниже доли вершины скопления", 0.70, 0.2, 0.95, 0.05));
        g.addRow(new BooleanDescriptor(FIT_ETH, "Разметка: подгонка по телам всех свечей сессии (ETH + RTH); профиль — только RTH", true));
        g.addRow(new DoubleDescriptor(PTGT, "Разметка: ширина куска ≈, п. (кусок шире 10 п. делится)", 8.0, 2, 10, 0.5));
        g.addRow(new BooleanDescriptor(CLUST, "Разметка: зоны — скопления тел свечей (границы на провалах), без нарезки равными кусками", true));
        g.addRow(new DoubleDescriptor(CL_DIP, "Разметка: провал между скоплениями от доли меньшей вершины", 0.10, 0.02, 0.6, 0.02));
        g.addRow(new DoubleDescriptor(CL_MAX, "Разметка: скопление не шире, п. (шире — остаётся самая плотная часть)", 8, 2, 10, 0.5));
        g.addRow(new BooleanDescriptor(SUB_ADD, "Композиты-уточнения внутри баланса добавляют свои зоны", false));
        g.addRow(new BooleanDescriptor(NODES, "Ядро композита — узлами профиля (без разметки)", true));
        g.addRow(new DoubleDescriptor(NODE_DIP, "Узлы: провал между вершинами от, TPO", 2.0, 0.5, 20, 0.5));
        g.addRow(new DoubleDescriptor(NODE_DROP, "Узлы: строки не ниже вершины узла на, TPO", 2.0, 0.5, 20, 0.5));
        g.addRow(new DoubleDescriptor(JOIN_GAP, "Соседние зоны одного вида ближе, п. — подтверждение старой (−1 — выкл.)", 0.25, -1, 10, 0.25));
        g = tab.addGroup("Первородные HVN-зоны (config.yaml)");
        g.addRow(new DoubleDescriptor(HVN_SHARE, "HVN-полоса: TPO ≥ доли максимума (и ядро композита)", 0.70, 0.3, 1.0, 0.05));
        g.addRow(new IntegerDescriptor(HVN_MIN, "Первородные: HVN в VA от, TPO", 8, 1, 30, 1));
        g.addRow(new IntegerDescriptor(HVN_MAX, "Первородные: HVN в VA до, TPO", 10, 1, 30, 1));
        g.addRow(new IntegerDescriptor(GHVN_MIN, "Серые: HVN в VA от, TPO", 5, 1, 30, 1));
        g.addRow(new IntegerDescriptor(GHVN_MAX, "Серые: HVN в VA до, TPO", 7, 1, 30, 1));
        g.addRow(new DoubleDescriptor(MERGE_OV, "Пересечение для склейки, доля", 0.50, 0.05, 1.0, 0.05));
        g.addRow(new DoubleDescriptor(MERGE_W, "Ширина первородной зоны до, п.", 10.0, 1, 50, 0.5));
        g.addRow(new IntegerDescriptor(GREY_HIST, "Молодая первородная серая, пока истории < дней", 2, 0, 20, 1));
        g.addRow(new DoubleDescriptor(GREY_WMIN, "Серые: ширина от, п.", 1.0, 0, 50, 0.25));
        g.addRow(new DoubleDescriptor(GREY_WMAX, "Серые: ширина до, п.", 10.0, 0, 50, 0.25));
        g.addRow(new DoubleDescriptor(REJ_SHARE, "Класс HVN: доля отторжений в одну сторону", 0.60, 0.5, 1.0, 0.05));
        g = tab.addGroup("Фильтры страницы карты зон");
        g.addRow(new IntegerDescriptor(NEAR, "Ближайших к open сессии зон (0 — все)", 0, 0, 100, 1));
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
        g.addRow(new IntegerDescriptor(OP_MAIN, "HVN-зоны", 30, 0, 100, 1));
        g.addRow(new IntegerDescriptor(OP_OLD, "Старые балансы", 30, 0, 100, 1));
        g.addRow(new IntegerDescriptor(OP_GREY, "Серые", 30, 0, 100, 1));
        g.addRow(new BooleanDescriptor(EDGES, "Линии границ", false));
        g.addRow(new IntegerDescriptor(OP_EDGE, "Непрозрачность границ, %", 85, 0, 100, 5));
        g = tab.addGroup("Вчерашний RTH-профиль");
        g.addRow(new BooleanDescriptor(PREV, "Показывать VAH / VAL / POC вчера", true));
        g.addRow(new ColorDescriptor(PREV_VA, "VAH / VAL", new Color(0x8a9097)));
        g.addRow(new ColorDescriptor(PREV_POC, "POC", new Color(0xe2662c)));

        tab = sd.addTab("Расчёт");
        g = tab.addGroup("Данные");
        g.addRow(new BarSizeDescriptor(BAR30, "Бары профиля (TPO-период)", BarSize.minute(30)));
        g.addRow(new IntegerDescriptor(HISTORY_DAYS, "Загружать историю, календарных дней", 1500, 60, 6000, 30));
        g.addRow(new BooleanDescriptor(ROLL_RAW, "Роллы: расчёт в ценах графика — гэп настоящий, цены не сдвигаются (выкл. — склейка)", true));
        g.addRow(new BooleanDescriptor(ROLL, "Склейка роллов: зоны до смены контракта сдвигаются на гэп ролла (ряд MotiveWave не склеен)", true));
        g.addRow(new DoubleDescriptor(ROLL_GAP, "Ролл: скачок между соседними 30m-барами от, п. (окно 10 дней до 3-й пятницы мар / июн / сен / дек)", 20.0, 1, 500, 1));
        g.addRow(new StringDescriptor(TZ, "Часовой пояс сессий", "America/Chicago"));
        g.addRow(new StringDescriptor(RTH_START, "RTH начало (ЧЧ:ММ)", "08:30"));
        g.addRow(new StringDescriptor(RTH_END, "RTH конец (ЧЧ:ММ)", "15:00"));
        g.addRow(new StringDescriptor(ETH_START, "Начало Globex-сессии (ЧЧ:ММ)", "17:00"));
        g.addRow(new DoubleDescriptor(TICK, "Тик (0 — из инструмента)", 0, 0, 100, 0.01));
        g = tab.addGroup("Профиль");
        g.addRow(new DoubleDescriptor(VA_PCT, "Value Area, доля", 0.70, 0.5, 0.95, 0.01));

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
                    + (res.rawPrices ? ", роллов найдено " + res.rollMs.length + " (цены графика)" : ", роллов склеено " + res.rollMs.length)
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
        p.rollAdjust = st.getBoolean(ROLL, true);
        p.rollRaw = st.getBoolean(ROLL_RAW, true);
        p.rollMinGapPts = st.getDouble(ROLL_GAP, 20.0);
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
        p.compositeMode = st.getBoolean(MODE, true);
        p.zoneMaxWidthPts = st.getDouble(ZONE_W, 10.0);
        p.coreByVa = st.getBoolean(CORE_VA, false);
        p.coreFromSessions = st.getBoolean(CORE_SESS, true);
        p.coreMinDaysShare = st.getDouble(CORE_DAYS, 0.5);
        p.coreCliffTpo = st.getInteger(CLIFF, 4);
        p.subOverlap = st.getDouble(SUB_OV, 0.70);
        p.compositeShortDays = st.getBoolean(SHORT_DAYS, true);
        p.compVaOverlap = st.getDouble(COMP_VA, 0.5);
        p.compJoinBoth = st.getBoolean(COMP_AND, true);
        p.compInsideShare = st.getDouble(COMP_IN, 0.8);
        p.compOutsideDays = st.getBoolean(COMP_OUT, true);
        p.compFalseBreak = st.getBoolean(FALSE_BRK, true);
        p.liveComposite = st.getBoolean(LIVE, true);
        p.compRollBreak = st.getBoolean(ROLL_BRK, true);
        p.compVaPrevDay = st.getBoolean(VA_PREV, true);
        p.sessResyncLater = st.getBoolean(RESYNC, false);
        p.drawOneColor = st.getBoolean(ONE_COLOR, true);
        p.drawExtendDays = st.getInteger(EXT_DAYS, 7);
        p.coreNodes = st.getBoolean(NODES, true);
        p.partition = st.getBoolean(PART, true);
        p.bodyShare = st.getDouble(BODY, 0.15);
        p.partCoreShare = st.getDouble(PCORE, 0.65);
        p.partTargetPts = st.getDouble(PTGT, 8.0);
        p.partMaxBodyPts = st.getDouble(PMAXB, 100);
        p.fitBodyShare = st.getDouble(FIT_B, 0.20);
        p.fitClusterShare = st.getDouble(FIT_C, 0.70);
        p.fitEthBodies = st.getBoolean(FIT_ETH, true);
        p.bodyClusters = st.getBoolean(CLUST, true);
        p.sessionZones = st.getBoolean(SESS, true);
        p.sessMinPeak = st.getInteger(S_PEAK, 3);
        p.sessMinGapPts = st.getDouble(S_GAP, 0.5);
        p.sessMaxPts = st.getDouble(S_MAX, 4.0);
        p.sessRefine = st.getBoolean(REFINE, true);
        p.sessCoreTouch = st.getBoolean(CORE_TOUCH, true);
        p.coreNearestBlue = st.getBoolean(CORE_NEAR, true);
        p.sessMinor = st.getBoolean(MINOR, true);
        p.sessMinorPromote = st.getBoolean(MINOR_PROMOTE, true);
        p.sessPeakShare = st.getDouble(S_SHARE, 0.25);
        p.sessBodyShare = st.getDouble(S_BODY, 0.5);
        p.clusterDip = st.getDouble(CL_DIP, 0.10);
        p.clusterMaxPts = st.getDouble(CL_MAX, 8);
        p.subAddsZones = st.getBoolean(SUB_ADD, false);
        p.nodeDipTpo = st.getDouble(NODE_DIP, 2.0);
        p.nodeDropTpo = st.getDouble(NODE_DROP, 2.0);
        p.noOverlap = st.getBoolean(NO_OVERLAP, true);
        p.gridPts = st.getDouble(GRID, 1.0);
        p.gridOffsetPts = st.getDouble(GRID_OFF, 0);
        p.gridNarrowGrey = st.getBoolean(GRID_GREY, true);
        p.gridNarrowKeepBlue = st.getBoolean(GRID_BLUE, true);
        p.oneSquareGrey = st.getBoolean(ONE_SQ, true);
        p.oneSquareMaxPts = st.getDouble(ONE_SQ_MAX, 2.0);
        p.compRejGrey = st.getBoolean(CR, true);
        p.compRejRatio = st.getDouble(CR_RATIO, 2.0);
        p.compRejMin = st.getInteger(CR_MIN, 2);
        p.invertGrey = st.getBoolean(INV, true);
        p.invertGapPts = st.getDouble(INV_GAP, 3.0);
        p.chartNoOverlap = st.getBoolean(CHART_NO_OV, true);
        p.minPiecePts = st.getDouble(MIN_PIECE, 1.0);
        p.subMaxRatio = st.getDouble(SUB_RATIO, 2.0);
        p.joinGapPts = st.getDouble(JOIN_GAP, 0.25);
        p.minWickTicks = st.getInteger(WICK, 2);
        p.sessions = st.getInteger(SESSIONS, 250);
        p.nearest = st.getInteger(NEAR, 0);
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
        boolean edges = st.getBoolean(EDGES, false);
        int edgeA = st.getInteger(OP_EDGE, 85);
        Map<Kind, Color> fill = new EnumMap<>(Kind.class), line = new EnumMap<>(Kind.class);
        for (Kind k : Kind.values()) {
            Color c = st.getColor(COLOR.get(k));
            if (c == null) c = DEF_COLOR.get(k);
            int op = k == Kind.OLD_BALANCE ? st.getInteger(OP_OLD, 30) : k == Kind.GREY ? st.getInteger(OP_GREY, 30) : st.getInteger(OP_MAIN, 30);
            fill.put(k, alpha(c, op));
            line.put(k, alpha(c, edgeA));
        }
        Stroke solid = new BasicStroke(1f);
        Color none = new Color(0, 0, 0, 0);
        List<Figure> figs = new ArrayList<>();
        for (ZonePlan.Rect r : plan.rects) {
            // куски разметки стоят вплотную: верх на 1 тик ниже, чтобы соседние полосы читались отдельно
            double top = r.z.k == ZoneEngine.K_PART && r.hi - r.lo > 2 * p.tick ? r.hi - p.tick : r.hi;
            Box b = new Box(r.t0, top, r.t1, r.lo);
            b.setFillColor(fill.get(r.kind));
            b.setLineColor(none);
            b.setUnderlay(true);
            b.setPopupMessage(r.tip);
            figs.add(b);
            if (!edges) continue;
            for (double y : new double[]{r.hi, r.lo}) {
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
