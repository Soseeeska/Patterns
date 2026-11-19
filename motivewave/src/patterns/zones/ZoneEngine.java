package patterns.zones;

import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/**
 * Расчёт карты зон TPO Zones на чистой Java, без зависимостей от MotiveWave (тестируется вне платформы).
 *
 * Режим по умолчанию (1.8: compositeMode + sessionZones; принцип действия — motivewave/README.md):
 *   1. роллы: находятся по гэпу между соседними 30m-барами; расчёт в ценах графика (rollRaw) или в склейке;
 *   2. RTH-дни из 30m-баров, TPO-профиль дня (POC, VA по Далтону);
 *   3. композиты: день входит по close в диапазоне И наслоению VA (внутренний / внешний день — ИЛИ), ложный
 *      выход доклеивает, ролл рвёт; ≥ 3 дней и VA дней задевают ядро — баланс; внутри раннего баланса —
 *      уточнение; формирующийся композит задаёт роли с 3-го дня;
 *   4. сессионные зоны: узлы RTH-профиля дня → мнимая зона → скопления тел 30m-свечей сессии → края по
 *      квадратам TPO; узел рядом с существующей зоной — её подтверждение; слабые узлы — минорные (серые);
 *   5. роль: у ядра композита — синяя, выше — красная, ниже — зелёная; вне композита — по отторжениям в
 *      сессию рождения;
 *   6. по сессиям: зоны не налезают (младшая не рисуется), раскладка в ценах графика (одна зона на уровне),
 *      серые: колеблющиеся в своём композите, узкие, стык «зелёные над красными».
 * Прежние режимы (1.1–1.6) — первородные HVN-зоны с классом по отторжениям, «старые балансы», разметка тела
 * композита, фильтры страницы карты зон (scripts/zone_map_export.py + zone_map_template.html) — включаются
 * настройками и по умолчанию не рисуются. Все цены профиля — в целых тиках, как в tpo/profile.py.
 */
public final class ZoneEngine {

    // ------------------------------------------------------------------ виды зон
    public static final int K_OLD_BALANCE = 0, K_HVN = 1, K_CORE = 2, K_PART = 3, K_SESS = 4, K_GREY = 5;

    public enum Kind {
        HVN_S("HVN · поддержка"), HVN_R("HVN · сопротивление"), HVN_RANGE("HVN · проторговка"),
        HVN_NEW("HVN · без истории"), OLD_BALANCE("Старый баланс (VA композита)"),
        GREY("Серая (мало истории / узкая)"), CORE("Проторговка композита");
        public final String title;
        Kind(String t) { title = t; }
    }

    // ------------------------------------------------------------------ параметры
    /** Все пороги. Значение по умолчанию задаётся здесь и в TpoZonesStudy (descriptor + getXxx(KEY, def)) — менять в обоих
     *  местах. Индикатор сам ставит compositeMode = true и zoneMaxWidthPts = 10. */
    public static final class Params {
        // ---- данные, сессии, роллы
        public double tick = 0.25;
        public ZoneId tz = ZoneId.of("America/Chicago");
        public int rthStartMin = 8 * 60 + 30, rthEndMin = 15 * 60;   // RTH 08:30–15:00 CT
        public int ethStartMin = 17 * 60;                              // Globex с 17:00 — дата следующего дня
        public int periodMinutes = 30;
        public int fullDayPeriods = 13;
        public boolean rollAdjust = true;        // роллы ищутся: сырой ряд (MotiveWave) рвётся на смене контракта
        public boolean rollRaw = true;           // расчёт в ценах графика: роллы только находятся (рвут композиты), цены не сдвигаются —
                                                 // гэп ролла настоящий, зоны взаимодействуют так, как видны на графике (иначе — склейка:
                                                 // цены до ролла сдвигаются на гэп, расчёт в ценах текущего контракта)
        public double rollMinGapPts = 20;        // ролл — скачок между соседними 30m-барами не меньше, п. (в окне 10 дней до 3-й пятницы
                                                 // мар / июн / сен / дек; обычный скачок между соседними барами — 1–2 п.)

        // ---- профиль дня
        public double valueAreaPct = 0.70;
        public double hvnShare = 0.70;            // HVN-полоса / ядро композита — строки не ниже этой доли максимума TPO
        public int minWickTicks = 2;              // хвост свечи (отторжение) — от стольких тиков

        // ---- композиты
        public boolean compositeMode = false;     // класс зоны по положению в композите (индикатор: true), иначе — по отторжениям (1.1)
        public int compositeMinDays = 4;          // 1.8.4: от 4 дней (было 3)
        public int smearMaxTpo = 5;               // 1.8.4: «размазня» — полный RTH-день, у которого в самой широкой строке (квадрат 1 п.)
                                                  // не больше стольких TPO (тренд, жирной части нет): в композит не входит; 0 — выкл.
        // ---- композит по стоимости (1.8.4, compValue): день входит, если его стоимость в стоимости композита — POC дня внутри
        //      VA композита или VA дня на ≥ compValueShare своей ширины внутри VA композита; день шире compDayMaxAdr средних
        //      диапазонов и «размазня» не входят; композит не шире compMaxAdr средних диапазонов; один день мимо (вылет) —
        //      пропускается, композит идёт дальше, если следующий день вернулся; два подряд — композит закрыт (с вылета).
        public boolean compValue = false;
        public double compValueShare = 0.5;
        public double compDayMaxAdr = 1.5;
        public int compExcursion = 1;
        public double compMaxAdr = 0;             // 1.8.4: композит не шире стольких средних дневных диапазонов (20 прошлых полных
                                                  // RTH-дней): день, с которым High–Low композита вышел бы шире, — выход; 0 — выкл.
        public int smearMode = 1;                 // ... 1 — такой день закрывает композит (выход, ложного выхода через него нет) и
                                                  // нового не начинает; 2 — пропускается, композит идёт дальше, как будто дня нет
        public boolean compositeShortDays = true; // короткие дни (ранние закрытия) входят в профиль композита, как в TPO платформы
        public double compVaOverlap = 0.5;        // VA дня и VA композита пересекаются на ≥ эту долю меньшей из двух (0 — не проверять)
        public boolean compJoinBoth = true;       // склейка: close в диапазоне И пересечение VA (иначе — ИЛИ); так же доклейка после
                                                  // ложного выхода
        public double compInsideShare = 0.8;      // ... но день, чей High–Low на ≥ эту долю внутри диапазона композита, клеится по ИЛИ
                                                  // (0 — выкл.)
        public boolean compOutsideDays = true;    // ... и внешний день: диапазон композита на ≥ ту же долю внутри High–Low дня
        public boolean compVaPrevDay = true;      // наслоение VA — с VA кандидата или с VA предыдущего (полного) дня кандидата
        public boolean compFalseBreak = true;     // ложный выход: возврат close в диапазон закрытого композита, пока новый не набрал
                                                  // compositeMinDays дней, — дни доклеиваются к нему
        public double compFalseBreakMaxPts = 100; // ... если диапазон закрытого композита не шире, п.
        public int compFalseBreakMax = 2;         // ... и не больше стольких продолжений подряд
        public boolean compRollBreak = true;      // ролл разрывает композит: кандидат закрывается на смене контракта, ложный выход через
                                                  // ролл не доклеивает
        public boolean compShortNoBreak = false;  // короткий день не рвёт кандидата: входит в профиль, выходом быть не может (отвергнуто)
        public boolean coreFromSessions = true;   // ядро — от POC композита до обрыва TPO; баланс — если VA дней задевают ядро
        public int coreCliffTpo = 4;              // обрыв: строка на N принтов ниже максимума предыдущего 1 п. (4 тика)
        public double coreMinDaysShare = 0.5;     // ядро признаётся, если его задевают VA ≥ этой доли дней композита; иначе это не
                                                  // баланс — композит не используется (0 — выкл.)
        public boolean coreByVa = false;          // ядро композита — VA (иначе HVN-полоса вокруг POC)
        public double zoneMaxWidthPts = 0;        // > 0: ядро композита (и HVN-полоса дня) шире — сужается до окна с POC и максимумом
                                                  // TPO (индикатор: 10 п.)
        public double bodyShare = 0.15;           // тело композита — строки не ниже этой доли максимума (сглаж. 1 п.), хвосты отпадают;
                                                  // зона, чья середина в теле, получает роль от композита
        public double partCoreShare = 0.65;       // ядро тела — часть тела не ниже этой доли максимума (роль, если sessCoreTouch выкл.)
        public double partMaxBodyPts = 100;       // тело шире — порог тела поднимается, пока не уложится (остаётся плотная середина)
        public double subOverlap = 0.70;          // ≥ этой доли диапазона композита внутри раннего баланса — уточнение: своего баланса
                                                  // не даёт, ценой управляет родитель (0 — выкл.)
        public double subMaxRatio = 2.0;          // ... если тот баланс не шире композита больше чем в столько раз
        public boolean liveComposite = true;      // формирующийся композит: с compositeMinDays дней — со следующей сессии задаёт роли
                                                  // (снимок по уже прошедшим дням); пока формируется — пересинхронизация

        // ---- сессионные зоны (1.7–1.8)
        public boolean sessionZones = true;       // зоны из узлов RTH TPO каждой сессии, подогнанные по телам свечей; композит задаёт роль
        public double sessNodeDrop = 1.0;         // узел — строки не ниже вершины на столько TPO (сглаж. 1 п.)
        public double sessNodeDip = 0.15;         // провал между узлами — от доли меньшей вершины (и ≥ 1 TPO)
        public double sessPeakShare = 0.25;       // узел — вершина не ниже этой доли максимума TPO дня (0 — любая ≥ sessMinPeak)
        public int sessMinPeak = 3;               // вершина узла — не меньше стольких TPO
        public double sessBodyShare = 0.5;        // подгонка узла по телам: скопление — тела не ниже этой доли максимума тел
        public boolean sessRefine = true;         // уточнение: мнимая зона пошире (строки узла ≥ sessParentShare вершины, ± 1 п.), внутри —
                                                  // все скопления тел свечей сессии (не одно), каждое — зона
        public double sessParentShare = 0.7;      // ... мнимая зона: строки профиля не ниже этой доли вершины узла
        public double sessClusterMin = 0.5;       // ... скопление — вершина тел не ниже этой доли максимума тел в мнимой зоне (и ≥ 2 тел)
        public double sessClusterDip = 0.15;      // ... скопления разделены провалом ≥ этой доли меньшей вершины (и ≥ 1.5 тела)
        public double sessSiblingGapPts = 1.0;    // ... зоны из одной мнимой могут стоять ближе sessMinGapPts — от стольких п.
        public double sessMaxPts = 4;             // зона не шире, п.
        // ---- пороги в волатильности (1.8.2): ядро композита (zoneMaxWidthPts), ширина сессионной зоны (sessMaxPts) и предел
        //      ложного выхода (compFalseBreakMaxPts) × средний диапазон 20 прошлых полных RTH-дней / volRefPts. Сетка TPO
        //      (gridPts) и зазоры не масштабируются — это квадраты графика.
        public boolean volScale = true;
        public double volRefPts = 54.5;           // медиана RTH-дня 2024–2026 (период, на котором настраивались пороги)
        // ---- зоны экстремумов (1.8.3): поддержка на лое / сопротивление на хае Globex-сессии, где цену отбросили, — обычные
        //      сессионные зоны (красная / зелёная, вправо до края, одна полоса), только из другого источника:
        //      · экстремум подтверждён: цена ушла от него на max(extAwayPts, extAwayAdr × средний диапазон 20 прошлых сессий)
        //        раньше, чем два close подряд за зоной; зона — со следующей сессии;
        //      · края по телам, тени снаружи: внутри мнимой зоны у экстремума (extBandShare диапазона сессии, 1.5 … extBandMaxPts п.)
        //        скопление тел; если там одни тени — ближайшая к экстремуму стопка краёв тел (≥ 2 в пределах 1 п., не дальше
        //        порога «ушли»), одиночное тело — не край; затем подгонка (extFit) по квадратам TPO;
        //      · только у краёв баланса (extEdgeOnly): поддержка не выше VAL своего баланса (+ extEdgeTolPts), сопротивление не
        //        ниже VAH; вне балансов — всегда;
        //      · обычные зоны в приоритете: экстремум встаёт только на свободное место (после всех обычных зон, в раскладке
        //        последним); рядом с зоной (ближе sessMinGapPts) — её подтверждение (зона не меняется, заметка в подсказке);
        //      · под вершиной в price discovery (extArea: выше всех прежних цен, откат ≤ 2 порогов «ушли») — подтверждение вершины;
        //      · вне серых правил, синей не бывает; цвет — композит по ядру / хвост баланса / сторона экстремума.
        public boolean extremes = true;
        public double extBandShare = 0.15, extBandMaxPts = 5;
        public double extAwayPts = 8, extAwayAdr = 0.25;
        public boolean extEdgeOnly = true;
        public double extEdgeTolPts = 1;
        public boolean extArea = true;
        public boolean extFit = true;             // подгонка краёв по свечам сессии экстремума: + тело под краем, тень наружу; + край тела
        public double extFitMinPts = 2;           // в зоне; − 2 × тело прошило край. Ширина ≥ extFitMinPts, ≤ max(его, 1.5 × исходной)
        public double extFitSearchPts = 2;        // и лимита зоны; край — в ± extFitSearchPts от исходного
        // ---- хвост баланса (1.8.3): зона в диапазоне баланса, но вне его тела — над телом красная, под телом зелёная (иначе — по
        //      сессии рождения: у вершины над балансом выходили «поддержки»)
        public boolean tailRole = true;
        // ---- серые шире greyMaxPts (по виду на последнюю сессию) не рисуются (1.8.3); 0 — выкл.
        public double greyMaxPts = 5;
        // ---- пробный патч 1.8.4
        //      · зона не шире minZonePts (по виду на последнюю сессию) не рисуется, кроме синей; 0 — выкл.;
        //      · узкие (≤ oneSquareMaxPts = 2 п.) — серые: oneSquareGrey включён (синие и экстремумы остаются);
        //      · узел шире предела (sessMaxPts × волатильность) режется не по центру, а до окна с максимумом тел (densestCrop);
        //      · стопка — зоны подряд по цене со щелью ≤ stackGapPts: от низа до верха не выше stackMaxPts; остаются (синие первыми)
        //        самые проторгованные (средний TPO на строку в RTH-днях с рождения самой ранней зоны стопки), прочие не рисуются; 0 — выкл.;
        //      · ядро по новому композиту (coreByNewer): новый композит, сложившийся на ценах старого баланса, перекрашивает
        //        все зоны в своём теле, в т. ч. прежнюю синюю (у его ядра синяя, выше красные, ниже зелёные); уточнений
        //        (subOverlap) и склейки ядер нет — у каждого композита своё ядро.
        public double minZonePts = 1;
        public boolean densestCrop = true;
        public double stackMaxPts = 10, stackGapPts = 1;
        public boolean coreByNewer = true;
        public double sessMinGapPts = 0.5;        // узел ближе этого к существующей зоне (или пересекает её) — её подтверждение
        public double gridPts = 1.0;              // квадрат TPO на графике, п. (tickInterval 4): края зоны — на границах квадратов, в ценах
                                                  // графика; после подгонки по телам край — к ближайшей границе, пополам — квадрат целиком
                                                  // (принт в приоритете); 0 — выкл.
        public double gridOffsetPts = 0;          // ... сдвиг сетки квадратов от целых, п.
        public boolean gridNarrowGrey = false;     // скопление тел уже одного квадрата (gridPts; без сетки — 1 п.) — зона в один квадрат,
                                                  // всегда серая
        public boolean sessMinor = true;          // минорные (серые) зоны: слабый узел сессии (вершина < sessPeakShare главного или
                                                  // < sessMinPeak TPO) — серая зона там, где нет других
        public int sessMinorMinPeak = 2;          // ... вершина минорного узла — от стольких TPO (тонкий профиль тренда: 1–2 TPO на строку)
        public boolean sessMinorPromote = true;   // сильный узел на месте минорной зоны её не обрывает, а продолжает: зона идёт дальше без
                                                  // разрыва и с этой сессии — обычная (роль — как посчитана при её рождении); иначе — вытесняет
        public int sessRoleMode = 0;              // роль вне композита: 0 — отторжения в сессию рождения, затем её закрытие; 1 — только закрытие
        public boolean sessCoreTouch = true;      // синяя — только зона, задевающая ядро композита (≤ zoneMaxWidthPts), а не всё ядро тела
        public boolean coreNearestBlue = true;    // ядро баланса не задевает ни одна зона этого композита — синей становится ближайшая к ядру
                                                  // из них (реальная зона, а не нарисованное ядро)
        public boolean sessResyncLater = false;   // зону синхронизирует и более поздний композит, сложившийся на ней (отвергнуто: синие
                                                  // копятся); иначе — только первый композит после её рождения

        // ---- серые и цвет (1.8)
        public boolean gridNarrowKeepBlue = true; // узкие (gridNarrowGrey, oneSquareGrey) — кроме синей: зона у ядра композита остаётся синей
        public boolean oneSquareGrey = true;       // узкая зона на графике (не шире oneSquareMaxPts; в т. ч. суженная раскладкой) — всегда серая
        public double oneSquareMaxPts = 2.0;      // ... узкая — не шире стольких п.
        public boolean compRejGrey = true;        // красная / зелёная, колеблющаяся в своём композите, — серая: в днях композита, задавшего ей
                                                  // цвет (с её появления), ≥ compRejMin отторжений и перевес за цвет (красная — снизу, зелёная —
                                                  // сверху) меньше compRejRatio : 1; не тестировалась (меньше compRejMin) — цвет остаётся
        public double compRejRatio = 2.0;
        public int compRejMin = 2;
        public boolean primaryByCore = true;      // первичный композит (price discovery): ≥ primaryShare его диапазона за пределами всех прежних цен
                                                  // (выше прежнего максимума или ниже минимума) — его зоны только по положению к ядру (у ядра синяя,
                                                  // выше красная, ниже зелёная), правила серых на них не действуют; минорные — серые
        public double primaryShare = 0.3;
        public boolean compSync = true;           // синхронизация композита и сессионных зон в нём: в каждом узле профиля композита истинная —
                                                  // одна зона (самое проторгованное по профилю композита место, при равенстве — старшая; синяя у
                                                  // ядра забирает свой узел), остальные в узле и зоны в провалах между узлами — минорные (серые)
        public double syncDip = 0.15;             // ... узлы профиля композита: тело режется по провалам ≥ этой доли меньшей вершины
        public double syncDrop = 0.25;            // ... узел — строки не ниже вершины на эту долю её высоты
        public boolean syncVaEdges = true;        // ... плюс у краёв VA композита — по одной главной зоне снаружи VA: поддержка — ближайшая к VAL
                                                  // снизу, сопротивление — ближайшая к VAH сверху (цена торгуется от них, баланс — над / под ними)
        public double syncVaTolPts = 1.0;         // ... «снаружи» — зона заходит внутрь VA не больше чем на столько п.
        public boolean invertGrey = true;         // стык «зелёные над красными»: зелёная стоит сразу над красной (без серых / синих между)
                                                  // ближе invertGapPts — обе серые (роли разных композитов противоречат друг другу)
        public double invertGapPts = 3;           // ... щель стыка не больше, п. (цены графика)

        // ---- раскладка и рисование
        public boolean noOverlap = true;          // зоны в сессии не налезают друг на друга
        public boolean layoutDropYounger = true;  // ... младшая зона, налезающая на уложенную, не рисуется (а не обрезается впритык)
        public double minPiecePts = 1.0;          // обрезанная зона уже этого — не рисуется
        public boolean chartNoOverlap = true;     // на графике на одном ценовом уровне одна зона: раскладка в ценах графика (зона — в контракте
                                                  // сессии появления), старшая главная; младшая, налезающая на неё (ближе sessMinGapPts), сужается
                                                  // до самого широкого свободного куска (≥ minPiecePts), иначе не рисуется (в т. ч. через ролл)
        public boolean drawOneColor = true;       // зона рисуется одной полосой: цвет и границы — на последнюю сессию, где она видна
                                                  // (иначе — по участкам, как менялась по сессиям)
        public int drawExtendDays = 7;            // зоны, дошедшие до последней сессии, рисуются вправо ещё на столько дней после её планового
                                                  // конца (в пустое место справа от последней свечи, в лайве — дальше новых баров); 0 — до конца сессии
        public int sessions = 250;                // сколько последних сессий показывать

        // ---- прежние режимы (1.1–1.6): первородные HVN-зоны, «старые балансы», разметка композита, фильтры страницы
        public int greyCompositeMinDays = 2;      // композит из 2 дней — серая зона
        public int hvnMinTpo = 8, hvnMaxTpo = 10; // первородные зоны: дни с HVN в VA 8–10 TPO
        public double mergeOverlapMin = 0.50;
        public double mergeMaxWidthPts = 10.0;
        public int greyHvnMinTpo = 5, greyHvnMaxTpo = 7;
        public int greyMinHistoryDays = 2;
        public double greyMinWidthPts = 1.0, greyMaxWidthPts = 10.0;
        public double greyMaxOverlap = 0.30;      // серая скрывается, если на > этой доли ширины заходит на основную
        public double rejectionShare = 0.60;      // класс HVN-зоны: ≥ этой доли отторжений в одну сторону
        public double coreAbsorb = 0.30;          // зона, на > этой доли своей ширины лежащая на ядре, поглощается им
        public double joinGapPts = 0.25;          // зона того же вида ближе этого к более старой — её подтверждение; < 0 — выкл.
        public boolean partition = true;          // разметка композита: тело целиком режется на зоны (ядро / подд. / сопр.)
        public double partDipShare = 0.06;        // провал для нарезки — не мельче этой доли максимума (и не мельче 2 TPO)
        public double partTargetPts = 8, partMaxPts = 10, partMinPts = 2;
        public double fitBodyShare = 0.20;        // подгонка по телам: строки, где тел 30m-свечей дней композита меньше этой доли
                                                  // максимума (и меньше 3), — пустота (0 — без подгонки)
        public double fitClusterShare = 0.70;     // зона — скопление тел вокруг вершины: не ниже этой доли её
        public boolean fitEthBodies = true;       // подгонка по телам всех свечей сессии (ETH + RTH), профиль — только RTH
        public boolean bodyClusters = true;       // зоны — скопления тел свечей (вершины профиля тел, границы на провалах)
        public double clusterDip = 0.10;          // провал между скоплениями — не мельче этой доли меньшей вершины (и ≥ 1.5 тела)
        public double clusterMaxPts = 8;          // скопление шире — порог поднимается, остаётся самая плотная часть
        public boolean subAddsZones = false;      // уточнения внутри баланса добавляют свои зоны (иначе зоны даёт только главный)
        public boolean coreNodes = true;          // ядро рисуется узлами профиля (локальные вершины, разделённые провалами)
        public double nodeDipTpo = 2.0;           // провал между узлами — не меньше стольких TPO от обеих вершин (сглаж. 1 п.)
        public double nodeDropTpo = 2.0;          // узел — строки не ниже вершины узла на столько TPO
        public int alignSessions = 0;             // подгонка границ по телам за первые N сессий (0 — без подгонки)
        public double alignSearchFrac = 0.5, alignSearchMinPts = 2.0, alignWPlain = 0.5, alignMinScore = 3;
        public int alignMinWidthTicks = 2;
        public double alignMaxWidthPts = 10.0, alignMaxWidthMult = 1.5;
        public int promoteRejections = 3;         // перевод серой в основную по отторжениям за подгонку; 0 — не переводить
        public int nearest = 0;                   // фильтр: ближайших к open сессии зон; 0 — все
        public int minRejections = 0;
        public int purityMinPct = 0, purityMaxPct = 100;
        public int minConfirmations = 0;
        public double confirmNearPts = 0;         // подтверждение: зазор полосы HVN до зоны ≤ N п.
        public int minAgeDays = 0;
        public double maxWidthPts = 60;           // ≥ 60 — любая
        public final Set<Kind> types = new HashSet<>(Arrays.asList(
                Kind.HVN_S, Kind.HVN_R, Kind.HVN_RANGE, Kind.HVN_NEW, Kind.GREY, Kind.CORE));
        public double confirmStoreMaxPts = 10.0;  // MAX_NEAR экспорта
    }

    // ------------------------------------------------------------------ входные бары
    public static final class Bar {
        public final long t;                 // начало бара, epoch ms
        public final double o, h, l, c;
        public Bar(long t, double o, double h, double l, double c) { this.t = t; this.o = o; this.h = h; this.l = l; this.c = c; }
    }

    // ------------------------------------------------------------------ профиль
    static final class Profile {
        final int low; final int[] counts;
        Profile(int low, int[] counts) { this.low = low; this.counts = counts; }
        int high() { return low + counts.length - 1; }
        int max() { int m = 0; for (int v : counts) m = Math.max(m, v); return m; }
    }

    static Profile buildProfile(int[] lows, int[] highs) {
        int lo = Integer.MAX_VALUE, hi = Integer.MIN_VALUE;
        for (int i = 0; i < lows.length; i++) { lo = Math.min(lo, lows[i]); hi = Math.max(hi, highs[i]); }
        int n = hi - lo + 1;
        int[] diff = new int[n + 1];
        for (int i = 0; i < lows.length; i++) { diff[lows[i] - lo]++; diff[highs[i] - lo + 1]--; }
        int[] c = new int[n]; int acc = 0;
        for (int i = 0; i < n; i++) { acc += diff[i]; c[i] = acc; }
        return new Profile(lo, c);
    }

    static Profile merge(Profile a, Profile b) {
        int lo = Math.min(a.low, b.low), hi = Math.max(a.high(), b.high());
        int[] c = new int[hi - lo + 1];
        for (int i = 0; i < a.counts.length; i++) c[a.low - lo + i] += a.counts[i];
        for (int i = 0; i < b.counts.length; i++) c[b.low - lo + i] += b.counts[i];
        return new Profile(lo, c);
    }

    static int pocIndex(Profile p) {
        int[] c = p.counts; int m = p.max();
        double center = (c.length - 1) / 2.0, best = Double.MAX_VALUE; int bi = 0;
        for (int i = 0; i < c.length; i++) {
            if (c[i] != m) continue;
            double d = Math.abs(i - center);
            if (d < best) { best = d; bi = i; }
        }
        return bi;
    }

    /** (poc, val, vah) в тиках — Value Area по Далтону (пары строк). */
    static int[] valueArea(Profile p, double pct) {
        int[] c = p.counts; int n = c.length;
        int poc = pocIndex(p);
        long total = 0; for (int v : c) total += v;
        double target = pct * total;
        int lo = poc, hi = poc; long acc = c[poc];
        while (acc < target && (lo > 0 || hi < n - 1)) {
            long up = hi < n - 1 ? sum(c, hi + 1, Math.min(hi + 3, n)) : -1;
            long dn = lo > 0 ? sum(c, Math.max(lo - 2, 0), lo) : -1;
            if (up >= dn && up >= 0) {
                int nh = Math.min(hi + 2, n - 1);
                acc += sum(c, hi + 1, nh + 1); hi = nh;
                if (up == dn && lo > 0) { int nl = Math.max(lo - 2, 0); acc += sum(c, nl, lo); lo = nl; }
            } else {
                int nl = Math.max(lo - 2, 0); acc += sum(c, nl, lo); lo = nl;
            }
        }
        return new int[]{p.low + poc, p.low + lo, p.low + hi};
    }

    private static long sum(int[] c, int a, int b) { long s = 0; for (int i = a; i < b; i++) s += c[i]; return s; }

    /** HVN-полоса: строки вокруг POC с TPO ≥ share × максимум, непрерывно от POC. */
    static int[] hvnBand(Profile p, int pocTicks, double share) {
        int[] c = p.counts; int i = pocTicks - p.low; double thr = share * p.max();
        int lo = i, hi = i;
        while (lo > 0 && c[lo - 1] >= thr) lo--;
        while (hi < c.length - 1 && c[hi + 1] >= thr) hi++;
        return new int[]{p.low + lo, p.low + hi};
    }

    // ------------------------------------------------------------------ RTH-день
    static final class Day {
        LocalDate date; int n; boolean shortDay;
        double open, high, low, close;
        final List<int[]> periods = new ArrayList<>();   // {period, lowTicks, highTicks, openTicks, closeTicks}
        final List<int[]> bodies = new ArrayList<>();    // тела всех 30m-свечей сессии (ETH + RTH): {open, close} в тиках
        final List<double[]> sbars = new ArrayList<>();  // все 30m-свечи сессии (ETH + RTH): {o, h, l, c}
        Profile profile; int poc, val, vah;              // тики
        int hvnTpo, bandLo, bandHi;                      // per_day (только полные дни)
        int maxSq;                                       // самая широкая строка профиля в квадратах 1 п., TPO
        double adr = Double.NaN;                         // средний диапазон 20 прошлых полных дней (без этого дня)
    }

    /** Масштаб порогов в пунктах: средний диапазон дней / volRefPts (volScale выкл. или нет данных — 1). */
    static double vs(Params p, List<Day> ds) {
        if (!p.volScale) return 1.0;
        double s = 0; int n = 0;
        for (Day d : ds) if (!Double.isNaN(d.adr)) { s += d.adr; n++; }
        return n == 0 ? 1.0 : (s / n) / p.volRefPts;
    }

    // ------------------------------------------------------------------ зона
    public static final class Zone {
        public int k; public double lo, hi; public LocalDate av; public String src;
        public boolean young; public LocalDate greyUntil;   // young && greyUntil == null — серая всё время
        public boolean fixedGrey;                            // ядро композита-уточнения: всегда серая
        public String info = "";                             // ядро: как посчитано (для подсказки)
        public List<Zone> nodes = List.of();                 // ядро: узлы профиля (рисуются вместо ядра)
        public Kind partKind; public Comp partOf;            // кусок разметки композита: вид и композит
        public Kind recolorKind; long recolorAt = Long.MAX_VALUE;   // сессионная зона: роль после (первой) перекраски композитом
        public LocalDate born;                               // сессионная зона: RTH-день, из узла которого она родилась
        public boolean minor;                                // сессионная минорная зона (слабый узел): всегда серая
        public boolean narrow;                               // сессионная зона уже одного квадрата TPO (gridNarrowGrey): всегда серая
        public boolean oneSquare;                            // сессионная зона не шире oneSquareMaxPts на графике (oneSquareGrey): серая
        public boolean inverted;                             // сведена в серую на стыке «зелёные над красными» (invertGrey)
        public boolean wavering;                             // серая: колеблется в своём композите (compRejGrey)
        public boolean syncMinor;                            // минорная по синхронизации с композитом (compSync)
        public int vaMajor;                                  // 1 — главная поддержка у VAL композита, 2 — главное сопротивление у VAH
        public boolean primaryColor;                         // цвет по ядру первичного композита (primaryByCore)
        public boolean nearCore;                             // синяя как ближайшая к ядру, которое не задевает ни одна зона (coreNearestBlue)
        long minorEnd = Long.MAX_VALUE;                      // ... с этой сессии на её месте сильный узел: продолжена обычной (или вытеснена)
        long[] rcAt = new long[0]; Kind[] rcKind = new Kind[0]; Comp[] rcComp = new Comp[0];   // все синхронизации: с сессии, роль, композит
        long hideFrom = Long.MAX_VALUE;                      // разметка: с этого дня зона внутри тела композита не рисуется
        Zone nodeOf;                                         // узел: его ядро
        int formationDays; List<LocalDate> conf = List.of();
        // события (дни от 1970-01-01)
        public long[] L = new long[0], S = new long[0];
        public final List<long[]> cf = new ArrayList<>();   // {ord, gap*100}
        public int ai;                                       // индекс av в списке полных RTH-дней
        public double blo, bhi;                              // границы после подгонки
        long[] coreConf = new long[0];                       // ядро: дни подтверждений другими композитами
        long[] govAv = new long[0]; Comp[] gov = new Comp[0]; // композиты, в диапазон которых попадает зона
        public long avOrd() { return av.toEpochDay(); }
        // зона экстремума (src = "ext")
        public boolean ext;                                  // сессионная зона из экстремума: вне серых правил, синей не бывает
        public boolean extRes;                               // сопротивление на хае (иначе поддержка на лое)
        public LocalDate extSrc;                             // сессия экстремума
        public double extPrice;                              // сам хай / лой сессии
        public final List<String> extNotes = new ArrayList<>();   // подтверждения экстремумами (подсказка)
        double extAway, extPrior; int extBar;                // порог «ушли»; прежний максимум / минимум цен; бар экстремума
        int extA, extE; double extCap;                       // бары сессии экстремума [extA, extE); лимит ширины, п.
    }

    /** Композит ≥ compositeMinDays: диапазон и его ядро (зона проторговки). */
    public static final class Comp {
        public double lo, hi; public LocalDate start, end, av; public int days; public Zone core;
        public Comp parent;                                  // уточнение внутри более раннего баланса
        public Comp extendsOf;                               // продолжение этого композита после ложного выхода
        public final List<LocalDate> dates = new ArrayList<>();   // дни профиля композита (без вылетов)
        public boolean live;                                 // снимок формирующегося композита
        public boolean primary;                              // первичный: в зоне price discovery (primaryByCore)
        public Profile prof; public int pocT;                // профиль композита и POC (тики) — для синхронизации с сессионными зонами
        List<int[]> cnodes;                                  // узлы профиля композита {lo, hi, вершина TPO} (тики), лениво
        public double bodyLo, bodyHi;                        // тело профиля (без хвостов)
        public double coreLo, coreHi;                        // ядро тела (строки не ниже partCoreShare максимума)
        List<int[]> segs = List.of();                        // разметка тела: {lo, hi, 0 ядро / 1 поддержка / 2 сопротивление}, тики
    }

    /** Состояние зоны на сессию. */
    public static final class State {
        public Comp comp;                                   // режим «по композиту»: правящий композит
        public Kind kind; public boolean promoted; public int rej, nl, ns, conf; public Double purity; public long age;
        public boolean recolored;                           // сессионная зона перекрашена композитом
    }

    public static final class Session {
        public LocalDate date; public long ord; public int firstBar, lastBar; public long startMs, endMs;
        public double open; public Double prevVah, prevVal, prevPoc; public LocalDate prevDate;
    }

    /** Видимая в сессию зона. */
    public static final class Shown {
        public final Zone z; public final State st;
        public double lo, hi;                               // границы на эту сессию (после обрезки пересечений)
        public Shown(Zone z, State st) { this.z = z; this.st = st; lo = z.blo; hi = z.bhi; }
    }

    public static final class Result {
        public final List<Zone> zones = new ArrayList<>();
        public final List<Session> sessions = new ArrayList<>();     // показываемые сессии
        public final List<List<Shown>> visible = new ArrayList<>();  // по сессиям
        public int rthDays, fullDays, composites;
        public final List<Comp> comps = new ArrayList<>();           // балансы (закрытые композиты, ядро признано)
        public final List<Comp> notBalance = new ArrayList<>();      // композиты, где дни не сошлись (без ядра)
        public final List<Comp> subComps = new ArrayList<>();        // уточнения внутри более раннего баланса
        public int thinDropped, stackDropped, stacksCut, smearDays;             // пробный патч 1.8.4: сколько убрано узких / из стопок, стопок урезано
        public final List<Comp> liveComps = new ArrayList<>();       // снимки формирующихся композитов (балансы, не уточнения)
        public long[] rollMs = new long[0];                          // роллы: начало первого бара нового контракта, epoch ms
        public double[] rollGap = new double[0];                     // ... и гэп (новый − старый), п.
        public boolean rawPrices;                                    // rollRaw: цены не сдвигались (rollShift = 0, расчёт в ценах графика)
        public List<Bar> bars = List.of();                           // бары расчёта (после склейки роллов, если она включена)
        public String note = "";
    }

    // ================================================================== расчёт
    public static Result run(List<Bar> bars, Params p) {
        Result res = new Result();
        double tick = p.tick;
        bars = new ArrayList<>(bars);
        bars.sort((a, b) -> Long.compare(a.t, b.t));
        if (p.rollAdjust) bars = rollAdjust(bars, p, res);
        res.bars = bars;

        // ---------- RTH-дни и сессии
        TreeMap<LocalDate, Day> days = new TreeMap<>();
        LocalDate[] sessOf = new LocalDate[bars.size()];
        boolean[] isRth = new boolean[bars.size()];
        LocalDate[] rthDateOf = new LocalDate[bars.size()];
        for (int i = 0; i < bars.size(); i++) {
            Bar b = bars.get(i);
            LocalDateTime lt = LocalDateTime.ofInstant(Instant.ofEpochMilli(b.t), p.tz);
            int m = lt.getHour() * 60 + lt.getMinute();
            LocalDate d = lt.toLocalDate();
            sessOf[i] = m >= p.ethStartMin ? d.plusDays(1) : d;
            if (m >= p.rthStartMin && m < p.rthEndMin) {
                isRth[i] = true; rthDateOf[i] = d;
                Day day = days.computeIfAbsent(d, x -> { Day y = new Day(); y.date = x; return y; });
                int per = (m - p.rthStartMin) / p.periodMinutes;
                if (day.periods.isEmpty()) { day.open = b.o; day.high = b.h; day.low = b.l; }
                day.high = Math.max(day.high, b.h); day.low = Math.min(day.low, b.l); day.close = b.c;
                day.periods.add(new int[]{per, ticks(b.l, tick), ticks(b.h, tick), ticks(b.o, tick), ticks(b.c, tick)});
            }
        }
        for (int i = 0; i < bars.size(); i++) {           // тела свечей всей сессии — для подгонки по телам
            Day d = days.get(sessOf[i]);
            if (d != null) {
                Bar bb = bars.get(i);
                d.bodies.add(new int[]{ticks(bb.o, tick), ticks(bb.c, tick)});
                d.sbars.add(new double[]{bb.o, bb.h, bb.l, bb.c});
            }
        }
        List<Day> all = new ArrayList<>(days.values());
        List<Day> full = new ArrayList<>();
        for (Day d : all) {
            d.n = d.periods.size();
            d.shortDay = d.n < p.fullDayPeriods;
            int[] lows = new int[d.n], highs = new int[d.n];
            for (int i = 0; i < d.n; i++) { lows[i] = d.periods.get(i)[1]; highs[i] = d.periods.get(i)[2]; }
            d.profile = buildProfile(lows, highs);
            Map<Integer, Integer> sq = new HashMap<>();
            for (int i = 0; i < d.n; i++)
                for (int q = (int) Math.floor(lows[i] * tick); q <= (int) Math.floor(highs[i] * tick - 1e-9); q++) sq.merge(q, 1, Integer::sum);
            for (int v : sq.values()) d.maxSq = Math.max(d.maxSq, v);
            int[] va = valueArea(d.profile, p.valueAreaPct);
            d.poc = va[0]; d.val = va[1]; d.vah = va[2];
            if (!d.shortDay) {
                full.add(d);
                int[] c = d.profile.counts; int mx = 0;
                for (int t = d.val; t <= d.vah; t++) mx = Math.max(mx, c[t - d.profile.low]);
                d.hvnTpo = mx;
                int[] band = narrow(d.profile, hvnBand(d.profile, d.poc, p.hvnShare), d.poc,
                        (int) Math.round(p.zoneMaxWidthPts / tick));
                d.bandLo = band[0]; d.bandHi = band[1];
            }
        }
        res.rthDays = all.size(); res.fullDays = full.size();
        for (int i = 0; i < full.size(); i++) {           // средний диапазон 20 прошлых полных дней (пороги в волатильности)
            int a0 = Math.max(0, i - 20); if (i - a0 < 5) continue;
            double s = 0; for (int k = a0; k < i; k++) s += full.get(k).high - full.get(k).low;
            full.get(i).adr = s / (i - a0);
        }
        if (full.size() < 3) { res.note = "мало RTH-дней в истории"; return res; }
        Map<LocalDate, LocalDate> nextTradeDay = new HashMap<>();
        for (int i = 0; i + 1 < all.size(); i++) nextTradeDay.put(all.get(i).date, all.get(i + 1).date);

        // ---------- композиты close_in_range
        List<Zone> ob = new ArrayList<>(), greyComp = new ArrayList<>();
        {
            List<Day> cand = new ArrayList<>(); Profile cp = null;
            List<Comp> live = res.liveComps;
            List<Day> lastCand = null; Profile lastCp = null; Comp lastComp = null, extOf = null;
            int extCount = 0, lastExt = 0;                  // сколько раз текущий / закрытый композит продолжался
            // режим «по композиту»: короткие дни (ранние закрытия) тоже входят в композит, как в TPO платформы
            if (p.compValue) valueComposites(p.compositeShortDays ? all : full, res, p, ob, greyComp, nextTradeDay);
            for (Day d : p.compValue ? List.<Day>of() : p.compositeMode && p.compositeShortDays ? all : full) {
                if (p.compositeMode && p.smearMaxTpo > 0 && !d.shortDay && d.maxSq <= p.smearMaxTpo) {   // размазня
                    res.smearDays++;
                    if (p.smearMode == 2 || cand.isEmpty()) continue;
                    Comp made = closeComposite(cand, cp, d.date, p, ob, greyComp, res.comps, res.notBalance);
                    if (made != null) made.extendsOf = extOf;
                    lastCand = null; lastCp = null; lastComp = made; extOf = null; lastExt = 0; extCount = 0;
                    cand = new ArrayList<>(); cp = null;
                    continue;
                }
                if (cand.isEmpty()) { cand.add(d); cp = d.profile; continue; }
                if (p.compositeMode && p.compRollBreak && rollBetween(res, p, cand.get(cand.size() - 1).date, d.date)) {
                    // ролл между последним днём кандидата и этим днём: кандидат закрывается, этот день открывает новый
                    Comp made = closeComposite(cand, cp, d.date, p, ob, greyComp, res.comps, res.notBalance);
                    if (made != null) made.extendsOf = extOf;
                    lastCand = null; lastCp = null; lastComp = made; extOf = null; lastExt = 0; extCount = 0;
                    cand = new ArrayList<>(); cand.add(d); cp = d.profile;
                    continue;
                }
                if (p.compositeMode && p.compShortNoBreak && d.shortDay) {        // короткий день — в профиль, не выход
                    cand.add(d); cp = merge(cp, d.profile); live(cand, cp, nextTradeDay.get(d.date), p, live); continue;
                }
                int c = ticks(d.close, tick);
                if (cand.size() >= p.compositeMinDays) lastCand = null;
                if (p.compositeMode && p.compFalseBreak && lastCand != null && c >= lastCp.low && c <= lastCp.high()
                        && (!p.compJoinBoth || vaJoins(d, lastCp, p) || vaPrev(d, lastCand, p) || insideDay(d, lastCp, p))
                        && (lastCp.high() - lastCp.low) * tick <= p.compFalseBreakMaxPts * vs(p, lastCand) && extCount < p.compFalseBreakMax
                        && !tooWide(cand.stream().map(x -> x.profile).reduce(lastCp, ZoneEngine::merge), d, p)) {
                    // ложный выход: композит закрылся, новый ещё не набрал дней, а цена закрылась обратно в его
                    // диапазоне — дни после выхода доклеиваются к нему, композит продолжается
                    extCount = lastExt;
                    List<Day> m = new ArrayList<>(lastCand); m.addAll(cand); m.add(d);
                    Profile mp = lastCp;
                    for (Day x : cand) mp = merge(mp, x.profile);
                    cand = m; cp = merge(mp, d.profile); extOf = lastComp; lastCand = null; extCount++;
                    live(cand, cp, nextTradeDay.get(d.date), p, live);
                    continue;
                }
                boolean join = c >= cp.low && c <= cp.high();
                boolean or = !p.compJoinBoth || insideDay(d, cp, p);          // день внутри композита — ИЛИ, иначе И
                boolean va = vaJoins(d, cp, p) || vaPrev(d, cand, p);
                if (p.compositeMode && !or) join = join && va;                  // close в диапазоне И наслоение VA
                else if (!join && p.compositeMode && p.compVaOverlap > 0) join = va;   // ... ИЛИ
                if (join && tooWide(cp, d, p)) join = false;
                if (join) { cand.add(d); cp = merge(cp, d.profile); live(cand, cp, nextTradeDay.get(d.date), p, live); continue; }
                Comp made = closeComposite(cand, cp, d.date, p, ob, greyComp, res.comps, res.notBalance);
                if (made != null) made.extendsOf = extOf;
                lastCand = cand; lastCp = cp; lastComp = made; extOf = null; lastExt = extCount; extCount = 0;
                cand = new ArrayList<>(); cand.add(d); cp = d.profile;
            }
            // незакрытый композит в конце данных не используется
        }
        res.composites = ob.size();

        // ---------- первородные HVN-зоны и серые из слабых HVN-дней
        List<Zone> prim = primaryZones(full, p.hvnMinTpo, p.hvnMaxTpo, p, nextTradeDay, K_HVN);
        List<Zone> greyHvn = primaryZones(full, p.greyHvnMinTpo, p.greyHvnMaxTpo, p, nextTradeDay, K_GREY);
        for (Zone z : prim) {
            int need = p.greyMinHistoryDays - z.formationDays;
            if (need <= 0) continue;
            z.young = true;
            z.greyUntil = z.conf.size() < need ? null : z.conf.get(need - 1).plusDays(1);
        }
        List<Zone> zones = res.zones;
        zones.addAll(ob);
        zones.addAll(prim);
        List<Zone> greyAll = new ArrayList<>(greyComp); greyAll.addAll(greyHvn);
        for (Zone z : greyAll) {
            double w = z.hi - z.lo;
            if (w >= p.greyMinWidthPts - 1e-9 && w <= p.greyMaxWidthPts + 1e-9) zones.add(z);
        }
        if (p.compositeMode) {
            // композит, чей диапазон почти целиком внутри более раннего баланса, — уточнение: своего баланса
            // не даёт, его ядро — серая зона, ценой в этом диапазоне управляет родитель
            if (p.subOverlap > 0 && !p.coreByNewer) {
                List<Comp> parents = new ArrayList<>();
                for (Comp c : res.comps) {
                    double w = c.hi - c.lo;
                    Comp par = null;
                    for (int i = parents.size() - 1; i >= 0 && par == null; i--) {
                        Comp q = parents.get(i);
                        double inter = Math.min(c.hi, q.hi) - Math.max(c.lo, q.lo);
                        if (inter >= p.subOverlap * w && q.hi - q.lo <= p.subMaxRatio * w) par = q;
                    }
                    if (c.extendsOf != null && parents.contains(c.extendsOf)) {   // продолжение после ложного выхода
                        parents.set(parents.indexOf(c.extendsOf), c); continue;
                    }
                    if (par == null) { parents.add(c); continue; }
                    c.parent = par;
                    res.subComps.add(c);
                    if (p.partition) continue;
                    Zone g = new Zone();
                    g.k = K_GREY; g.src = "sub"; g.fixedGrey = true; g.av = c.av; g.lo = c.core.lo; g.hi = c.core.hi;
                    zones.add(g);
                }
                res.comps.clear(); res.comps.addAll(parents);
                // снимок формирующегося композита внутри более раннего баланса — уточнение, ролей не задаёт
                res.liveComps.removeIf(c -> {
                    double w = c.hi - c.lo;
                    for (Comp q : parents) {
                        if (!q.av.isBefore(c.av) || (c.start.equals(q.start))) continue;   // только закрытые раньше, не он сам
                        double inter = Math.min(c.hi, q.hi) - Math.max(c.lo, q.lo);
                        if (inter >= p.subOverlap * w && q.hi - q.lo <= p.subMaxRatio * w) return true;
                    }
                    return false;
                });
            }
            // ядра: новое, пересекающее существующее ≥ mergeOverlapMin (от более узкого), его подтверждает
            List<Zone> cores = new ArrayList<>();
            Map<Zone, List<Long>> cc = new HashMap<>();
            for (Comp c : res.comps) {
                int lo = ticks(c.core.lo, tick), hi = ticks(c.core.hi, tick);
                Zone best = null; double bestOv = -1;
                for (Zone e : cores) {
                    double ov = overlap(ticks(e.lo, tick), ticks(e.hi, tick), lo, hi);
                    boolean near = p.joinGapPts >= 0 && Math.max(e.lo, c.core.lo) - Math.min(e.hi, c.core.hi) <= p.joinGapPts + 1e-9;
                    if ((ov >= p.mergeOverlapMin || near) && ov > bestOv) { best = e; bestOv = ov; }
                }
                if (best != null) { cc.get(best).add(c.av.toEpochDay()); if (!p.coreByNewer) c.core = best; }
                else { cores.add(c.core); cc.put(c.core, new ArrayList<>()); }
            }
            for (Zone e : cores) e.coreConf = cc.get(e).stream().mapToLong(Long::longValue).toArray();
            if (!p.partition) {
                for (Zone e : cores) {
                    if (e.nodes.isEmpty()) { zones.add(e); continue; }
                    for (Zone nz : e.nodes) { nz.coreConf = e.coreConf; zones.add(nz); }
                }
            } else {
                // разметка: первый композит на уровне — главный; совпадающий кусок позднего композита его подтверждает
                List<Comp> bal = new ArrayList<>(res.comps);
                if (p.subAddsZones) bal.addAll(res.subComps);
                bal.sort((x, y) -> x.av.compareTo(y.av));
                List<Zone> parts = new ArrayList<>();
                Map<Zone, List<Long>> pc = new HashMap<>();
                for (Comp c : bal) for (int[] sg : c.segs) {
                    Zone best = null; double bestOv = -1;
                    for (Zone e : parts) {
                        double ov = overlap(ticks(e.lo, tick), ticks(e.hi, tick), sg[0], sg[1]);
                        if (ov >= p.mergeOverlapMin && ov > bestOv) { best = e; bestOv = ov; }
                    }
                    if (best == null && p.joinGapPts >= 0) {        // вплотную к зоне того же вида — её подтверждение
                        int kind = sg[2];
                        for (Zone e : parts) {
                            int ek = e.partKind == Kind.CORE ? 0 : e.partKind == Kind.HVN_S ? 1 : 2;
                            double gap = Math.max(e.lo, sg[0] * tick) - Math.min(e.hi, sg[1] * tick);
                            if (ek == kind && gap <= p.joinGapPts + 1e-9) { best = e; break; }
                        }
                    }
                    if (best != null) { pc.get(best).add(c.av.toEpochDay()); continue; }
                    Zone z = new Zone();
                    z.k = K_PART; z.src = "part"; z.av = c.av; z.partOf = c; z.info = c.core.info;
                    z.partKind = sg[2] == 0 ? Kind.CORE : sg[2] == 1 ? Kind.HVN_S : Kind.HVN_R;
                    z.lo = sg[0] * tick; z.hi = sg[1] * tick;
                    parts.add(z); pc.put(z, new ArrayList<>());
                }
                for (Zone e : parts) { e.coreConf = pc.get(e).stream().mapToLong(Long::longValue).toArray(); zones.add(e); }
                // дневные HVN-зоны и серые внутри тела сложившегося композита — не рисуются
                for (Zone z : zones) {
                    if (z.k != K_HVN && z.k != K_GREY) continue;
                    double mid = (z.lo + z.hi) / 2;
                    for (Comp c : bal) if (c.bodyLo <= mid && mid <= c.bodyHi) z.hideFrom = Math.min(z.hideFrom, c.av.toEpochDay());
                }
            }
        }

        if (p.compositeMode && p.sessionZones) {
            // зоны из сессий: узлы RTH TPO дня, подогнанные по телам; роль — композит / отторжения / выход / серая
            List<Comp> bal = new ArrayList<>(res.comps);
            if (p.liveComposite) bal.addAll(res.liveComps);
            bal.sort((x, y) -> x.av.compareTo(y.av));
            zones.clear();
            List<Zone> ses = new ArrayList<>();
            Map<Zone, List<Long>> cf = new HashMap<>();
            Map<Zone, int[]> sibling = new HashMap<>();                 // зона → {день, мнимая зона} (уточнение)
            int onePt = (int) Math.round(1.0 / tick), maxW = (int) Math.round(p.sessMaxPts / tick);
            double mwk = p.minWickTicks * tick;
            List<Zone> extC = p.extremes ? extremeCandidates(bars, sessOf, days, res, p) : new ArrayList<>();
            for (int di = 0; di <= full.size(); di++) {
                if (di == full.size()) { for (Zone x : extC) placeExtreme(x, ses, cf, bal, res, p, days); break; }   // после обычных
                Day d = full.get(di);
                LocalDate av = nextTradeDay.get(d.date);
                if (av == null) continue;
                double gsh = rollShift(res, av.atStartOfDay(p.tz).toInstant().toEpochMilli());   // цены графика = цены расчёта − сдвиг
                for (int[] nd : dayNodes(d, p, onePt, (int) Math.round(maxW * vs(p, List.of(d))))) {
                    if (p.gridPts > 0) {                                // края — по квадратам профиля на графике
                        double glo = nd[0] * tick - gsh, ghi = nd[1] * tick - gsh;
                        if (nd[4] == 1) { double m = (glo + ghi) / 2; glo = m - 0.01; ghi = m + 0.01; }   // уже квадрата — один квадрат
                        double[] g = snapGrid(glo, ghi, p);
                        nd[0] = ticks(g[0] + gsh, tick); nd[1] = ticks(g[1] + gsh, tick);
                    }
                    Zone best = null;
                    long avd = av.toEpochDay();
                    for (Zone e : ses) {
                        if (e.minor && e.minorEnd <= avd && !p.sessMinorPromote) continue;   // вытесненная минорная — уровня не держит
                        double gap = Math.max(e.lo, nd[0] * tick) - Math.min(e.hi, nd[1] * tick);   // < 0 — пересекаются
                        int[] sb = sibling.get(e);                  // зона из той же мнимой зоны того же дня — ближе можно
                        boolean sis = p.sessRefine && sb != null && sb[0] == d.date.toEpochDay() && sb[1] == nd[2];
                        if (gap < (sis ? p.sessSiblingGapPts : p.sessMinGapPts) - 1e-9) { best = e; break; }
                    }
                    if (best != null && best.minor && nd[3] == 0 && p.sessMinorPromote) {
                        // сильный узел на месте минорной зоны — её продолжение: с этой сессии зона обычная, без разрыва
                        if (best.minorEnd > avd) best.minorEnd = avd;
                    } else if (best != null && best.minor && nd[3] == 0) {
                        // сильный узел на месте минорной зоны — вытесняет её: минорная пропадает, встаёт основная
                        best.minorEnd = avd;
                        best = null;
                        for (Zone e : ses) {
                            if (e.minor && e.minorEnd <= avd) continue;
                            double gap = Math.max(e.lo, nd[0] * tick) - Math.min(e.hi, nd[1] * tick);
                            int[] sb = sibling.get(e);
                            boolean sis = p.sessRefine && sb != null && sb[0] == d.date.toEpochDay() && sb[1] == nd[2];
                            if (gap < (sis ? p.sessSiblingGapPts : p.sessMinGapPts) - 1e-9) { best = e; break; }
                        }
                    }
                    if (best != null) { cf.get(best).add(av.toEpochDay()); continue; }
                    Zone z = new Zone();
                    z.k = K_SESS; z.src = "sess"; z.av = av; z.lo = nd[0] * tick; z.hi = nd[1] * tick;
                    z.minor = nd[3] == 1; z.born = d.date; z.narrow = p.gridNarrowGrey && nd[4] == 1;
                    sibling.put(z, new int[]{(int) d.date.toEpochDay(), nd[2]});
                    double mid = (z.lo + z.hi) / 2;
                    Comp gov = null;
                    for (Comp c : bal) if (!c.av.isAfter(av) && c.bodyLo <= mid && mid <= c.bodyHi) gov = c;
                    Comp tail = null;
                    if (gov == null && p.tailRole)
                        for (Comp c : res.comps) if (!c.av.isAfter(av) && c.lo <= mid && mid <= c.hi && c.bodyHi > c.bodyLo) tail = c;
                    z.partKind = gov != null ? roleOf(gov, z, p) : tail != null ? (mid > tail.bodyHi ? Kind.HVN_R : Kind.HVN_S)
                            : roleByDay(d, z.lo, z.hi, mwk, p.sessRoleMode);
                    if (gov != null) z.partOf = gov;
                    // синхронизация с композитом: один композит (первый, накрывший зону после рождения, или формирующийся, в
                    // котором она родилась); пока он формируется — каждый снимок, после закрытия — заморожено
                    LocalDate grp = gov != null && gov.live ? gov.start : null;
                    List<Long> at = new ArrayList<>(); List<Kind> kd = new ArrayList<>(); List<Comp> cc = new ArrayList<>();
                    for (Comp c : bal) {
                        if (!c.av.isAfter(av) || c.bodyLo > mid || mid > c.bodyHi) continue;
                        if (!p.sessResyncLater && !p.coreByNewer) {
                            if (grp == null) grp = c.start;
                            if (!c.start.equals(grp)) continue;
                        }
                        // баланс не перекрашиваем: синяя зона другому (более позднему) композиту не отдаётся; внутри своего
                        // формирующегося композита — пересинхронизация, как прежде
                        if (p.sessResyncLater && !kd.isEmpty() && kd.get(kd.size() - 1) == Kind.CORE
                                && !c.start.equals(cc.get(cc.size() - 1).start)) break;
                        if (p.sessResyncLater && kd.isEmpty() && z.partKind == Kind.CORE && gov != null
                                && !c.start.equals(gov.start)) break;
                        at.add(c.av.toEpochDay()); kd.add(roleOf(c, z, p)); cc.add(c);
                        if (!c.live && !p.sessResyncLater && !p.coreByNewer) break;
                    }
                    if (!at.isEmpty()) {
                        z.rcAt = at.stream().mapToLong(Long::longValue).toArray(); z.rcKind = kd.toArray(new Kind[0]); z.rcComp = cc.toArray(new Comp[0]);
                        z.recolorAt = z.rcAt[0]; z.recolorKind = z.rcKind[0]; z.info = cc.get(cc.size() - 1).core.info;
                    }
                    ses.add(z); cf.put(z, new ArrayList<>());
                }
            }
            if (p.sessCoreTouch && p.coreNearestBlue) nearestToCoreBlue(res.comps, ses);
            for (Zone z : ses) { z.coreConf = cf.get(z).stream().mapToLong(Long::longValue).toArray(); zones.add(z); }
        }

        // ---------- события по RTH-барам: отторжения (лонг / шорт)
        List<Integer> rthIdx = new ArrayList<>();
        for (int i = 0; i < bars.size(); i++) if (isRth[i]) rthIdx.add(i);
        int nR = rthIdx.size();
        double[] RO = new double[nR], RH = new double[nR], RL = new double[nR], RC = new double[nR];
        long[] RD = new long[nR];
        Map<LocalDate, Integer> firstRth = new HashMap<>();
        for (int j = 0; j < nR; j++) {
            Bar b = bars.get(rthIdx.get(j));
            RO[j] = b.o; RH[j] = b.h; RL[j] = b.l; RC[j] = b.c;
            LocalDate d = rthDateOf[rthIdx.get(j)];
            RD[j] = d.toEpochDay();
            firstRth.putIfAbsent(d, j);
        }
        double mw = p.minWickTicks * tick;
        long[] fullOrd = new long[full.size()];
        for (int i = 0; i < full.size(); i++) fullOrd[i] = full.get(i).date.toEpochDay();
        for (Zone z : zones) {
            Integer s = firstRth.get(z.av);
            if (s != null) {
                List<Long> lg = new ArrayList<>(), sh = new ArrayList<>();
                for (int j = s; j < nR; j++) {
                    int t = interaction(RO[j], RH[j], RL[j], RC[j], z.lo, z.hi, mw);
                    if (t == 1) lg.add(RD[j]); else if (t == 2) sh.add(RD[j]);
                }
                z.L = lg.stream().mapToLong(Long::longValue).toArray();
                z.S = sh.stream().mapToLong(Long::longValue).toArray();
            }
            // подтверждения: зачётные HVN-дни (8–10 TPO) после появления, зазор до зоны
            for (Day d : full) {
                if (d.date.isBefore(z.av)) continue;
                if (d.hvnTpo < p.hvnMinTpo || d.hvnTpo > p.hvnMaxTpo) continue;
                double gap = Math.max(0, Math.max(d.bandLo * tick - z.hi, z.lo - d.bandHi * tick));
                if (gap <= p.confirmStoreMaxPts) z.cf.add(new long[]{d.date.toEpochDay(), Math.round(gap * 100)});
            }
            z.ai = lowerBound(fullOrd, z.avOrd());
        }

        // ---------- сессии (все бары), подгонка границ
        List<Session> sessAll = new ArrayList<>();
        for (int i = 0; i < bars.size(); i++) {
            Session cur = sessAll.isEmpty() ? null : sessAll.get(sessAll.size() - 1);
            if (cur == null || !cur.date.equals(sessOf[i])) {
                cur = new Session(); cur.date = sessOf[i]; cur.ord = cur.date.toEpochDay();
                cur.firstBar = i; cur.open = bars.get(i).o; cur.startMs = bars.get(i).t;
                sessAll.add(cur);
            }
            cur.lastBar = i; cur.endMs = bars.get(i).t + p.periodMinutes * 60_000L;
        }
        long[] sessOrd = new long[sessAll.size()];
        for (int i = 0; i < sessAll.size(); i++) sessOrd[i] = sessAll.get(i).ord;
        for (Zone z : zones) {
            z.blo = z.lo; z.bhi = z.hi;
            if (p.alignSessions <= 0) continue;
            int i0 = lowerBound(sessOrd, z.avOrd());
            int i1 = Math.min(i0 + p.alignSessions, sessAll.size());
            if (i1 <= i0) continue;
            int a = sessAll.get(i0).firstBar, e = sessAll.get(i1 - 1).lastBar + 1;
            double w = z.hi - z.lo;
            double search = Math.max(p.alignSearchFrac * w, p.alignSearchMinPts);
            double maxW = Math.max(p.alignMaxWidthPts, p.alignMaxWidthMult * w);
            if (p.compositeMode && p.zoneMaxWidthPts > 0) maxW = Math.min(maxW, Math.max(w, p.zoneMaxWidthPts));
            double[] r = alignZone(bars.subList(a, e), z.lo, z.hi, tick, search, mw, p.alignWPlain,
                    p.alignMinScore, p.alignMinWidthTicks * tick, maxW);
            z.blo = Math.rint(r[0] / tick) * tick; z.bhi = Math.rint(r[1] / tick) * tick;
        }

        if (p.compositeMode) {
            // ядро, нарисованное узлами, в списке зон нет: его границы — от нижнего до верхнего узла
            for (Comp c : res.comps) {
                Zone e = c.core;
                if (!zones.contains(e) && e.nodes.isEmpty()) { e.blo = e.lo; e.bhi = e.hi; }   // ядро не на графике (разметка)
                if (e.nodes.isEmpty()) continue;
                e.blo = Double.MAX_VALUE; e.bhi = -Double.MAX_VALUE;
                for (Zone nz : e.nodes) { e.blo = Math.min(e.blo, nz.blo); e.bhi = Math.max(e.bhi, nz.bhi); }
            }
            for (Zone z : zones) {
                if (z.k != K_HVN && z.k != K_GREY) continue;
                double mid = (z.blo + z.bhi) / 2;
                List<Comp> g = new ArrayList<>();
                for (Comp c : res.comps) if (c.lo <= mid && mid <= c.hi) g.add(c);
                z.gov = g.toArray(new Comp[0]);
                z.govAv = g.stream().mapToLong(c -> c.av.toEpochDay()).toArray();
            }
        }

        // ---------- показываемые сессии и предыдущий RTH-день
        int from = Math.max(0, sessAll.size() - Math.max(1, p.sessions));
        for (int i = from; i < sessAll.size(); i++) {
            Session s = sessAll.get(i);
            int k = lowerBound(fullOrd, s.ord) - 1;
            if (k >= 0) {
                Day y = full.get(k);
                s.prevDate = y.date; s.prevVah = y.vah * tick; s.prevVal = y.val * tick; s.prevPoc = y.poc * tick;
            }
            res.sessions.add(s);
        }

        // ---------- фильтры страницы
        Set<Zone> keep = new LinkedHashSet<>();
        for (Session s : res.sessions) {
            List<double[]> cand = new ArrayList<>();   // {zoneIndex, dist}
            for (int zi = 0; zi < zones.size(); zi++) {
                Zone z = zones.get(zi);
                if (z.avOrd() > s.ord) continue;
                State st = stateAt(z, s.ord, p, fullOrd);
                if (!passes(z, st, p)) continue;
                double dist = s.open < z.blo ? z.blo - s.open : s.open > z.bhi ? s.open - z.bhi : 0;
                cand.add(new double[]{zi, dist});
            }
            cand.sort((x, y) -> Double.compare(x[1], y[1]));      // стабильная, как Array.sort
            int n = p.nearest > 0 ? Math.min(p.nearest, cand.size()) : cand.size();
            for (int i = 0; i < n; i++) keep.add(zones.get((int) cand.get(i)[0]));
        }
        for (Session s : res.sessions) {
            List<Shown> list = new ArrayList<>();
            for (Zone z : keep) {
                if (z.avOrd() > s.ord) continue;
                State st = stateAt(z, s.ord, p, fullOrd);
                if (st.kind != null) list.add(new Shown(z, st));      // null — поглощена ядром композита
            }
            List<Shown> out = new ArrayList<>();
            for (Shown v : list) {
                // режим «по композиту»: зона, лежащая на ядре, — его подтверждение
                boolean hide = false;
                if (p.compositeMode && v.st.kind != Kind.CORE && v.z.k != K_PART && v.z.k != K_SESS) {
                    for (Shown m : list) {
                        if (m.st.kind != Kind.CORE) continue;
                        double inter = Math.min(v.z.bhi, m.z.bhi) - Math.max(v.z.blo, m.z.blo);
                        if (inter > 0 && inter / Math.max(v.z.bhi - v.z.blo, tick) > p.coreAbsorb) { hide = true; break; }
                    }
                    if (hide) continue;
                }
                // зона вплотную к более старой видимой зоне того же вида — её подтверждение
                if (p.compositeMode && p.joinGapPts >= 0 && v.st.kind != Kind.CORE && v.z.k != K_PART && v.z.k != K_SESS) {
                    for (Shown m : list) {
                        if (m == v || m.st.kind != v.st.kind) continue;
                        boolean older = m.z.avOrd() < v.z.avOrd() || (m.z.avOrd() == v.z.avOrd() && list.indexOf(m) < list.indexOf(v));
                        if (older && Math.max(m.z.blo, v.z.blo) - Math.min(m.z.bhi, v.z.bhi) <= p.joinGapPts + 1e-9) { hide = true; break; }
                    }
                    if (hide) continue;
                }
                // зона из серого источника — подтверждение первородной зоны под ней
                boolean secondary = v.st.kind == Kind.GREY || (p.compositeMode && v.z.k == K_GREY);
                if (!secondary) { out.add(v); continue; }
                for (Shown m : list) {
                    if (m.st.kind == Kind.GREY || (p.compositeMode && m.z.k == K_GREY)) continue;
                    double inter = Math.min(v.z.bhi, m.z.bhi) - Math.max(v.z.blo, m.z.blo);
                    if (inter > 0 && inter / Math.max(v.z.bhi - v.z.blo, tick) > p.greyMaxOverlap) { hide = true; break; }
                }
                if (!hide) out.add(v);
            }
            if (p.compositeMode && p.noOverlap) out = layout(out, p);
            res.visible.add(out);
        }
        // ---------- после раскладки по сессиям, по порядку: одна зона на ценовом уровне графика → колеблющиеся в композите
        //            серые → узкие серые → стык «зелёные над красными» серый → зоны первичного композита — по его ядру →
        //            синхронизация с профилем композита: истинные / минорные, главные у краёв VA (каждый шаг видит цвета
        //            после предыдущего)
        if (p.compositeMode && p.chartNoOverlap) chartLayout(res, p);
        if (p.compositeMode && p.compRejGrey) compRejToGrey(res, p);
        if (p.compositeMode && p.oneSquareGrey) narrowToGrey(res, p);
        if (p.compositeMode && p.invertGrey) invertedToGrey(res, p);
        if (p.compositeMode && p.sessionZones && p.primaryByCore) primaryByCore(res, p);
        if (p.compositeMode && p.sessionZones && p.compSync) compSync(res, p);
        if (p.compositeMode && p.greyMaxPts > 0) dropWideGrey(res, p);
        if (p.compositeMode && p.minZonePts > 0) dropThin(res, p);
        if (p.compositeMode && p.stackMaxPts > 0) stackBudget(res, p, all);
        return res;
    }

    // ------------------------------------------------------------------ зоны экстремумов (1.8.3)
    /** Серые шире greyMaxPts не рисуются: решение по виду и ширине зоны на последнюю сессию, где она видна (как её рисует
     *  ZonePlan одной полосой), — зона убирается целиком, без обрывков. */
    static void dropWideGrey(Result res, Params p) {
        Map<Zone, Shown> last = new HashMap<>();
        for (List<Shown> l : res.visible) for (Shown v : l) last.put(v.z, v);
        Set<Zone> drop = new HashSet<>();
        for (Shown v : last.values()) if (v.st.kind == Kind.GREY && v.hi - v.lo > p.greyMaxPts + 1e-9) drop.add(v.z);
        if (drop.isEmpty()) return;
        for (List<Shown> l : res.visible) l.removeIf(v -> drop.contains(v.z));
    }

    /** Окно шириной w внутри [a, b] с максимумом тел (occ); при равенстве — ближе к середине. Начало окна. */
    static int densest(int[] occ, int a, int b, int w) {
        int best = (a + b) / 2 - w / 2; long bs = -1; double mid = (a + b) / 2.0, bo = Double.MAX_VALUE;
        for (int s = a; s + w <= b; s++) {
            long sum = 0; for (int t = s; t <= s + w && t < occ.length; t++) sum += occ[t];
            double off = Math.abs(s + w / 2.0 - mid);
            if (sum > bs || (sum == bs && off < bo)) { bs = sum; best = s; bo = off; }
        }
        return best;
    }

    /** Узкие (не шире minZonePts по виду на последнюю сессию) не рисуются, кроме синих: зона убирается целиком. */
    static void dropThin(Result res, Params p) {
        Map<Zone, Shown> last = new HashMap<>();
        for (List<Shown> l : res.visible) for (Shown v : l) last.put(v.z, v);
        Set<Zone> drop = new HashSet<>();
        for (Shown v : last.values()) if (v.st.kind != Kind.CORE && v.hi - v.lo <= p.minZonePts + 1e-9) drop.add(v.z);
        res.thinDropped = drop.size();
        if (!drop.isEmpty()) for (List<Shown> l : res.visible) l.removeIf(v -> drop.contains(v.z));
    }

    /** Стопки по виду на последнюю сессию: зоны подряд по цене со щелью ≤ stackGapPts; стопка выше stackMaxPts (от низа до
     *  верха, со щелями) — первыми берутся синие, затем остальные, каждые по проторгованности (средний TPO на строку в RTH-днях
     *  с рождения самой ранней зоны стопки), пока ни одна стопка из оставленных не выше stackMaxPts; остальные не рисуются
     *  (целиком). */
    static void stackBudget(Result res, Params p, List<Day> days) {
        Map<Zone, Shown> last = new LinkedHashMap<>();
        for (List<Shown> l : res.visible) for (Shown v : l) last.put(v.z, v);
        List<Shown> vs = new ArrayList<>(last.values());
        vs.sort(Comparator.comparingDouble(v -> v.lo));
        Set<Zone> drop = new HashSet<>();
        List<List<Shown>> runs = new ArrayList<>(); List<Shown> cur = new ArrayList<>(); double top = -1e18;
        for (Shown v : vs) {
            if (!cur.isEmpty() && v.lo - top > p.stackGapPts + 1e-9) { runs.add(cur); cur = new ArrayList<>(); }
            cur.add(v); top = cur.size() == 1 ? v.hi : Math.max(top, v.hi);
        }
        if (!cur.isEmpty()) runs.add(cur);
        for (List<Shown> r : runs) {
            double lo0 = Double.MAX_VALUE, hi0 = -Double.MAX_VALUE;
            for (Shown v : r) { lo0 = Math.min(lo0, v.lo); hi0 = Math.max(hi0, v.hi); }
            if (hi0 - lo0 <= p.stackMaxPts + 1e-9) continue;
            res.stacksCut++;
            LocalDate from = null;
            for (Shown v : r) if (from == null || v.z.av.isBefore(from)) from = v.z.av;
            Map<Shown, Double> dens = new HashMap<>();
            for (Shown v : r) {
                int t0 = ticks(v.lo, p.tick), t1 = ticks(v.hi, p.tick); double tot = 0;
                for (Day d : days) {
                    if (d.date.isBefore(from)) continue;
                    int[] c = d.profile.counts;
                    for (int t = Math.max(t0, d.profile.low); t <= Math.min(t1, d.profile.low + c.length - 1); t++) tot += c[t - d.profile.low];
                }
                dens.put(v, tot / (t1 - t0 + 1));
            }
            List<Shown> order = new ArrayList<>(r);
            order.sort((x, y) -> x.st.kind == Kind.CORE ^ y.st.kind == Kind.CORE ? (x.st.kind == Kind.CORE ? -1 : 1)
                    : Double.compare(dens.get(y), dens.get(x)));
            List<Shown> kept = new ArrayList<>();
            for (Shown v : order) {
                kept.add(v);
                if (kept.size() > 1 && maxSpan(kept, p.stackGapPts) > p.stackMaxPts + 1e-9) { kept.remove(v); drop.add(v.z); }
            }
        }
        res.stackDropped = drop.size();
        if (!drop.isEmpty()) for (List<Shown> l : res.visible) l.removeIf(v -> drop.contains(v.z));
    }

    /** Самая широкая стопка (зоны подряд со щелью ≤ gap) среди данных: размах от низа до верха, п. */
    static double maxSpan(List<Shown> zs, double gap) {
        List<Shown> v = new ArrayList<>(zs); v.sort(Comparator.comparingDouble(x -> x.lo));
        double best = 0, lo = 0, hi = -Double.MAX_VALUE;
        for (Shown x : v) {
            if (x.lo - hi > gap + 1e-9) lo = x.lo;
            hi = Math.max(hi, x.hi); best = Math.max(best, hi - lo);
        }
        return best;
    }

    /** Зона экстремума у края баланса: поддержка не выше VAL, сопротивление не ниже VAH (± extEdgeTolPts) последнего
     *  баланса, известного к её появлению (закрытый до этого дня или формирующийся на этот день), чей диапазон её накрывает. */
    static boolean atBalanceEdge(Zone z, Result res, Params p) {
        double mid = (z.lo + z.hi) / 2;
        Comp best = null;
        for (Comp c : res.comps) if (c.av.isBefore(z.av) && c.lo <= mid && mid <= c.hi && (best == null || c.end.isAfter(best.end))) best = c;
        for (Comp c : res.liveComps) if (c.av != null && c.av.equals(z.av) && c.lo <= mid && mid <= c.hi && (best == null || c.end.isAfter(best.end))) best = c;
        if (best == null || best.prof == null) return true;   // вне балансов — край сам по себе
        int[] va = valueArea(best.prof, p.valueAreaPct);
        return z.extRes ? z.lo >= va[2] * p.tick - p.extEdgeTolPts : z.hi <= va[1] * p.tick + p.extEdgeTolPts;
    }

    /** Кандидаты зон экстремумов: экстремум Globex-сессии, подтверждённый уходом цены (как в extremeZones), с краями
     *  по телам свечей внутри мнимой зоны у экстремума (верхние extBandShare диапазона сессии, 1.5 … extBandMaxPts п.): профиль тел,
     *  обрезанных мнимой зоной, — строки не ниже половины максимума вокруг самой плотной ближе к экстремуму; тени — снаружи.
     *  Ширина ≤ sessMaxPts × масштаб волатильности. Только у краёв балансов (extEdgeOnly). По порядку появления. */
    static List<Zone> extremeCandidates(List<Bar> bars, LocalDate[] sessOf, TreeMap<LocalDate, Day> days, Result res, Params p) {
        List<int[]> ss = new ArrayList<>();                    // {first, last} баров сессии
        for (int i = 0; i < bars.size(); i++) {
            if (ss.isEmpty() || !sessOf[i].equals(sessOf[ss.get(ss.size() - 1)[0]])) ss.add(new int[]{i, i}); else ss.get(ss.size() - 1)[1] = i;
        }
        int n = bars.size(); double tick = p.tick;
        int[] sessIdx = new int[n];
        double[] rng = new double[ss.size()], priorHi = new double[ss.size()], priorLo = new double[ss.size()];
        double runHi = -Double.MAX_VALUE, runLo = Double.MAX_VALUE;
        for (int i = 0; i < ss.size(); i++) {
            priorHi[i] = runHi; priorLo[i] = runLo;
            double h = -Double.MAX_VALUE, l = Double.MAX_VALUE;
            for (int j = ss.get(i)[0]; j <= ss.get(i)[1]; j++) { sessIdx[j] = i; h = Math.max(h, bars.get(j).h); l = Math.min(l, bars.get(j).l); }
            rng[i] = h - l; runHi = Math.max(runHi, h); runLo = Math.min(runLo, l);
        }
        List<Zone> out = new ArrayList<>();
        for (int i = 0; i < ss.size(); i++) {
            int a = ss.get(i)[0], e = ss.get(i)[1] + 1;
            double r = rng[i];
            if (r <= 0) continue;
            double band = Math.min(p.extBandMaxPts, Math.max(1.5, p.extBandShare * r));
            double adr = r;
            if (i >= 5) { double s = 0; int k0 = Math.max(0, i - 20); for (int k = k0; k < i; k++) s += rng[k]; adr = s / (i - k0); }
            double away = Math.max(p.extAwayPts, p.extAwayAdr * adr);
            LocalDate sd = sessOf[a];
            Day day = days.get(sd);
            double scale = day != null ? vs(p, List.of(day)) : 1.0;
            int cap = Math.max(1, (int) Math.round(p.sessMaxPts * scale / tick));
            for (boolean up : new boolean[]{true, false}) {
                double ext = up ? -Double.MAX_VALUE : Double.MAX_VALUE; int at = a;
                for (int j = a; j < e; j++) { Bar b = bars.get(j); if (up ? b.h > ext : b.l < ext) { ext = up ? b.h : b.l; at = j; } }
                int eT = ticks(ext, tick), bT = (int) Math.round(band / tick);
                int r0 = up ? eT - bT : eT, r1 = up ? eT : eT + bT;      // мнимая зона, тики
                int[] cnt = new int[r1 - r0 + 1];
                for (int j = a; j < e; j++) {
                    Bar b = bars.get(j);
                    int lo = ticks(Math.min(b.o, b.c), tick), hi = ticks(Math.max(b.o, b.c), tick);
                    for (int t = Math.max(lo, r0); t <= Math.min(hi, r1); t++) cnt[t - r0]++;
                }
                int mx = 0; for (int c : cnt) mx = Math.max(mx, c);
                int zl, zh;
                if (mx == 0) {                                         // у экстремума одни тени: зона — на ближайшей к нему стопке
                    List<Double> eg = new ArrayList<>();               // краёв тел (≥ 2 в пределах 1 п.), не дальше порога «ушли»
                    for (int j = a; j < e; j++) { Bar b = bars.get(j); eg.add(up ? Math.max(b.o, b.c) : Math.min(b.o, b.c)); }
                    eg.sort(up ? java.util.Comparator.reverseOrder() : java.util.Comparator.naturalOrder());
                    int one = (int) Math.round(1.0 / tick), found = -1;
                    for (int k = 0; k + 1 < eg.size() && found < 0; k++) {
                        if (Math.abs(eg.get(k) - ext) > away) break;
                        if (Math.abs(eg.get(k + 1) - eg.get(k)) <= 1.0 + 1e-9) found = k;
                    }
                    if (found < 0) continue;                           // одиночное тело — не край
                    int et = ticks(eg.get(found), tick);
                    zl = up ? et - one : et; zh = up ? et : et + one;
                } else {
                    int pk = -1;                                       // самая плотная строка, ближе к экстремуму
                    for (int k = 0; k < cnt.length; k++) if (cnt[k] == mx && (pk < 0 || (up ? k > pk : k < pk))) pk = k;
                    int k0 = pk, k1 = pk;
                    while (k0 > 0 && cnt[k0 - 1] * 2 >= mx) k0--;
                    while (k1 < cnt.length - 1 && cnt[k1 + 1] * 2 >= mx) k1++;
                    zl = r0 + k0; zh = r0 + k1;
                    if (zh - zl > cap) { if (up) zl = zh - cap; else zh = zl + cap; }
                }
                double lo = zl * tick, hi = zh * tick;
                int j0 = at + 1;
                if (j0 >= n) continue;
                int brk = n;
                for (int j = j0; j + 1 < n; j++) {
                    boolean b1 = up ? bars.get(j).c > hi : bars.get(j).c < lo, b2 = up ? bars.get(j + 1).c > hi : bars.get(j + 1).c < lo;
                    if (b1 && b2) { brk = j + 1; break; }
                }
                int conf = -1;
                for (int j = j0; j < brk; j++) if (up ? bars.get(j).l <= ext - away : bars.get(j).h >= ext + away) { conf = j; break; }
                if (conf < 0) continue;
                int si = sessIdx[conf] + 1;
                if (si >= ss.size()) continue;
                Zone z = new Zone();
                z.k = K_SESS; z.src = "ext"; z.ext = true; z.av = sessOf[ss.get(si)[0]]; z.born = sd;
                z.lo = lo; z.hi = hi; z.extRes = up; z.extSrc = sd; z.extPrice = ext;
                z.extAway = away; z.extPrior = up ? priorHi[i] : priorLo[i]; z.extBar = at;
                z.extA = a; z.extE = e; z.extCap = cap * tick;
                z.info = String.format(Locale.ROOT, "%.2f", away);
                if (!p.extEdgeOnly || atBalanceEdge(z, res, p)) out.add(z);
            }
        }
        out.sort((x, y) -> x.av.compareTo(y.av));
        return out;
    }

    /** Кандидат-экстремум → сессионная зона по общим правилам: края по квадратам TPO; рядом (ближе sessMinGapPts) с существующей
     *  зоной — её подтверждение (зона не двигается и не перекрашивается, в подсказке — заметка); под вершиной в price discovery —
     *  подтверждение вершины (extArea); иначе новая зона: роль — композит (тело) / хвост баланса / сторона экстремума. */
    static void placeExtreme(Zone x, List<Zone> ses, Map<Zone, List<Long>> cf, List<Comp> bal, Result res, Params p, TreeMap<LocalDate, Day> days) {
        double tick = p.tick;
        if (p.gridPts > 0) {
            double gsh = rollShift(res, x.av.atStartOfDay(p.tz).toInstant().toEpochMilli());
            double[] g = snapGrid(x.lo - gsh, x.hi - gsh, p);
            x.lo = Math.rint((g[0] + gsh) / tick) * tick; x.hi = Math.rint((g[1] + gsh) / tick) * tick;
            if (p.extFit) fitExtreme(x, res.bars, p, gsh);
        }
        long avd = x.av.toEpochDay();
        String note = String.format(Locale.ROOT, "подтверждена экстремумом: %s сессии %s %.2f", x.extRes ? "хай" : "лой", x.extSrc, x.extPrice);
        for (Zone e : ses) {                                    // обычные зоны — любого возраста (их раскладка не меняется), экстремумы — раньше
            if (e.ext && e.av.isAfter(x.av)) continue;
            if (e.minor && e.minorEnd <= avd && !p.sessMinorPromote) continue;
            double gap = Math.max(e.lo, x.lo) - Math.min(e.hi, x.hi);
            if (gap < p.sessMinGapPts - 1e-9) { cf.get(e).add(avd); e.extNotes.add(note); return; }
        }
        if (p.extArea)
            for (Zone k : ses) {
                if (!k.ext || k.extRes != x.extRes || k.av.isAfter(x.av)) continue;
                boolean disc = x.extRes ? k.extPrice > k.extPrior && x.extPrice <= k.extPrice && x.extPrice > k.extPrior
                                        : k.extPrice < k.extPrior && x.extPrice >= k.extPrice && x.extPrice < k.extPrior;
                if (!disc || x.extBar <= k.extBar) continue;
                double depth = 0;
                for (int j = k.extBar + 1; j < x.extBar; j++) depth = Math.max(depth, x.extRes ? k.lo - res.bars.get(j).l : res.bars.get(j).h - k.hi);
                if (depth <= 2 * Math.max(k.extAway, x.extAway)) { cf.get(k).add(avd); k.extNotes.add(note); k.extBar = x.extBar; return; }
            }
        double mid = (x.lo + x.hi) / 2;
        Comp gov = null, tail = null;
        for (Comp c : bal) if (!c.av.isAfter(x.av) && c.bodyLo <= mid && mid <= c.bodyHi) gov = c;
        if (gov == null && p.tailRole)
            for (Comp c : res.comps) if (!c.av.isAfter(x.av) && c.lo <= mid && mid <= c.hi && c.bodyHi > c.bodyLo) tail = c;
        Kind side = x.extRes ? Kind.HVN_R : Kind.HVN_S;            // экстремум не бывает синим: на ядре — по своей стороне
        x.partKind = gov != null ? roleOf(gov, x, p) : tail != null ? (mid > tail.bodyHi ? Kind.HVN_R : Kind.HVN_S) : side;
        if (x.partKind == Kind.CORE) x.partKind = side;
        if (gov != null) x.partOf = gov;
        LocalDate grp = gov != null && gov.live ? gov.start : null;
        List<Long> at = new ArrayList<>(); List<Kind> kd = new ArrayList<>(); List<Comp> cc = new ArrayList<>();
        for (Comp c : bal) {
            if (!c.av.isAfter(x.av) || c.bodyLo > mid || mid > c.bodyHi) continue;
            if (grp == null) grp = c.start;
            if (!c.start.equals(grp)) continue;
            Kind rk = roleOf(c, x, p);
            at.add(c.av.toEpochDay()); kd.add(rk == Kind.CORE ? side : rk); cc.add(c);
            if (!c.live) break;
        }
        if (!at.isEmpty()) {
            x.rcAt = at.stream().mapToLong(Long::longValue).toArray(); x.rcKind = kd.toArray(new Kind[0]); x.rcComp = cc.toArray(new Comp[0]);
            x.recolorAt = x.rcAt[0]; x.recolorKind = x.rcKind[0];
        }
        ses.add(x); cf.put(x, new ArrayList<>());
    }

    /** Подгонка краёв зоны-экстремума (extFit) по свечам его сессии, края — на квадратах TPO (цены графика). Сопротивление:
     *  + тело ниже верхнего края, а тень выходит выше; + верхушка тела в зоне; − 2 × тело выше верхнего края (прошило).
     *  Поддержка — зеркально. Ширина extFitMinPts … max(extFitMinPts, 1.5 × исходной), не шире лимита зоны; край — в ±
     *  extFitSearchPts от исходного и не за экстремумом. Лучший счёт; при равенстве — ближе к исходным краям, потом уже. */
    static void fitExtreme(Zone x, List<Bar> bars, Params p, double gsh) {
        double sq = p.gridPts, tick = p.tick;
        double lo0 = x.lo - gsh, hi0 = x.hi - gsh, w0 = hi0 - lo0, ext = x.extPrice - gsh;
        double minW = p.extFitMinPts, maxW = Math.min(Math.max(minW, 1.5 * w0), Math.max(minW, x.extCap));
        int steps = (int) Math.round(p.extFitSearchPts / sq);
        double bestS = -1e9, bestD = 1e9, bestW = 1e9, bl = lo0, bh = hi0;
        for (int i = -steps; i <= steps; i++) for (int j = -steps; j <= steps; j++) {
            double lo = lo0 + i * sq, hi = hi0 + j * sq, w = hi - lo;
            if (w < minW - 1e-9 || w > maxW + 1e-9) continue;
            if (x.extRes ? hi > Math.ceil(ext / sq) * sq + 1e-9 : lo < Math.floor(ext / sq) * sq - 1e-9) continue;
            double sc = 0;
            for (int k = x.extA; k < x.extE; k++) {
                Bar b = bars.get(k);
                double bt = Math.max(b.o, b.c) - gsh, bb = Math.min(b.o, b.c) - gsh, h = b.h - gsh, l = b.l - gsh;
                if (x.extRes) {
                    if (h < lo) continue;
                    if (bt <= hi && h > hi) sc += 1;
                    if (bt >= lo && bt <= hi) sc += 1;
                    if (bt > hi) sc -= 2;
                } else {
                    if (l > hi) continue;
                    if (bb >= lo && l < lo) sc += 1;
                    if (bb >= lo && bb <= hi) sc += 1;
                    if (bb < lo) sc -= 2;
                }
            }
            double d = Math.abs(lo - lo0) + Math.abs(hi - hi0);
            if (sc > bestS + 1e-9 || (Math.abs(sc - bestS) < 1e-9 && (d < bestD - 1e-9 || (Math.abs(d - bestD) < 1e-9 && w < bestW)))) {
                bestS = sc; bestD = d; bestW = w; bl = lo; bh = hi;
            }
        }
        x.lo = Math.rint((bl + gsh) / tick) * tick; x.hi = Math.rint((bh + gsh) / tick) * tick;
    }



    /** Узлы профиля композита: тело (строки ≥ bodyShare максимума вокруг POC, сглаж. 1 п.) режется по провалам ≥ syncDip
     *  меньшей вершины; узел — строки куска не ниже вершины на syncDrop её высоты. {lo, hi, вершина TPO} в тиках. */
    static List<int[]> compNodes(Comp c, Params p) {
        if (c.cnodes != null) return c.cnodes;
        Profile pf = c.prof; int[] cnt = pf.counts; int n = cnt.length, onePt = (int) Math.round(1.0 / p.tick), half = onePt / 2;
        double[] sm = new double[n];
        for (int i = 0; i < n; i++) {
            double sum = 0; int k = 0;
            for (int j = Math.max(0, i - half); j <= Math.min(n - 1, i + half); j++) { sum += cnt[j]; k++; }
            sm[i] = sum / k;
        }
        double mx = 0; for (double v : sm) mx = Math.max(mx, v);
        int pi = c.pocT - pf.low, a = pi, b = pi;
        while (a > 0 && sm[a - 1] >= p.bodyShare * mx) a--;
        while (b < n - 1 && sm[b + 1] >= p.bodyShare * mx) b++;
        List<int[]> pcs = new ArrayList<>();
        splitRel(sm, a, b, p.syncDip, 1.0, pcs);
        List<int[]> out = new ArrayList<>();
        for (int[] q : pcs) {
            int m = q[0]; for (int i = q[0]; i <= q[1]; i++) if (sm[i] > sm[m]) m = i;
            int lo = m, hi = m;
            while (lo > q[0] && sm[lo - 1] >= sm[m] * (1 - p.syncDrop)) lo--;
            while (hi < q[1] && sm[hi + 1] >= sm[m] * (1 - p.syncDrop)) hi++;
            out.add(new int[]{pf.low + lo, pf.low + hi, (int) Math.round(sm[m])});
        }
        c.cnodes = out;
        return out;
    }

    /** Синхронизация композита и сессионных зон (compSync), по последней сессии — на всю длину зоны (без разрывов). Зона
     *  относится к композиту, который задаёт ей цвет к последней сессии. В каждом узле профиля композита истинная — одна зона:
     *  синяя у ядра (если есть), иначе с наибольшим TPO композита на своих строках (при равенстве — старшая); остальные в узле
     *  и зоны в провалах между узлами — минорные, серые. syncVaEdges: у краёв VA композита — по одной главной зоне снаружи VA
     *  (заходит внутрь не больше syncVaTolPts): поддержка под ядром — ближайшая к VAL, сопротивление над ядром — ближайшая к
     *  VAH; они зелёная и красная, даже если были минорными или серыми. */
    static void compSync(Result res, Params p) {
        int n = res.visible.size(); if (n == 0) return;
        long o = res.sessions.get(n - 1).ord;
        Map<String, Shown> best = new HashMap<>(); Map<String, Integer> bestT = new HashMap<>(); Set<String> coreNode = new HashSet<>();
        List<Object[]> cand = new ArrayList<>();
        Map<LocalDate, Comp> comps = new HashMap<>(); Map<LocalDate, List<Shown>> mine = new HashMap<>();
        for (Shown v : res.visible.get(n - 1)) {
            Zone z = v.z;
            if (z.k != K_SESS || z.ext) continue;
            Comp cp = z.partOf;
            if (z.rcAt.length > 0) { int ri = lowerBound(z.rcAt, o + 1) - 1; if (ri >= 0) cp = z.rcComp[ri]; }
            if (cp == null || cp.prof == null) continue;
            comps.putIfAbsent(cp.start, cp); mine.computeIfAbsent(cp.start, x -> new ArrayList<>()).add(v);
            if (v.st.kind == Kind.GREY) continue;                      // серая остаётся серой
            int lo = ticks(z.lo, p.tick), hi = ticks(z.hi, p.tick), mid = (lo + hi) / 2;
            int node = -1; List<int[]> nds = compNodes(cp, p);
            for (int k = 0; k < nds.size(); k++) if (mid >= nds.get(k)[0] && mid <= nds.get(k)[1]) node = k;
            String key = cp.start + "#" + node;
            if (v.st.kind == Kind.CORE) { coreNode.add(key); continue; }   // синяя — всегда истинная, её узел — её
            if (node < 0) { z.syncMinor = true; continue; }
            int tpo = 0;
            for (int t = lo; t <= hi; t++) if (t >= cp.prof.low && t <= cp.prof.high()) tpo = Math.max(tpo, cp.prof.counts[t - cp.prof.low]);
            Shown b0 = best.get(key);
            if (b0 == null || tpo > bestT.get(key) || (tpo == bestT.get(key) && z.av.isBefore(b0.z.av))) { best.put(key, v); bestT.put(key, tpo); }
            cand.add(new Object[]{v, key});
        }
        for (Object[] q : cand) { Shown v = (Shown) q[0]; String key = (String) q[1]; if (coreNode.contains(key) || best.get(key) != v) v.z.syncMinor = true; }
        if (p.syncVaEdges) for (var e : mine.entrySet()) {
            Comp cp = comps.get(e.getKey());
            int[] va = valueArea(cp.prof, p.valueAreaPct);
            double val = va[1] * p.tick, vah = va[2] * p.tick + p.tick, cmid = (cp.core.lo + cp.core.hi) / 2, tol = p.syncVaTolPts + 1e-9;
            Shown sup = null, rs = null; double ds = Double.MAX_VALUE, dr = Double.MAX_VALUE;
            for (Shown v : e.getValue()) {
                Zone z = v.z;
                if (v.st.kind == Kind.CORE && !z.syncMinor) continue;
                double mid = (z.lo + z.hi) / 2;
                if (mid < cmid && z.hi <= val + tol && val - z.hi < ds) { ds = Math.max(0, val - z.hi); sup = v; }
                if (mid > cmid && z.lo >= vah - tol && z.lo - vah < dr) { dr = Math.max(0, z.lo - vah); rs = v; }
            }
            if (sup != null) { sup.z.syncMinor = false; sup.z.vaMajor = 1; }
            if (rs != null) { rs.z.syncMinor = false; rs.z.vaMajor = 2; }
        }
        for (List<Shown> l : res.visible) for (Shown v : l) {
            if (v.z.vaMajor == 1) v.st.kind = Kind.HVN_S;
            else if (v.z.vaMajor == 2) v.st.kind = Kind.HVN_R;
            else if (v.z.syncMinor) v.st.kind = Kind.GREY;
        }
    }

    /** Первичный композит (primaryByCore) — сложился в зоне price discovery: ≥ primaryShare его диапазона за пределами всех
     *  прежних цен (бары до его первого дня, цены графика). Зоны, которые он красит (синхронизированы с ним или его снимками
     *  к этой сессии, либо родились в нём), — только по положению к его ядру: задевает ядро (или ближайшая к ядру) — синяя,
     *  выше середины ядра — красная, ниже — зелёная; правила серых (узкие, колеблющиеся, стык) на них не действуют, минорные
     *  остаются серыми. Вторичный композит (цена там уже была) — по общим правилам. */
    static void primaryByCore(Result res, Params p) {
        Map<LocalDate, Boolean> prim = new HashMap<>();
        List<Comp> all = new ArrayList<>(res.comps); all.addAll(res.liveComps);
        all.sort((x, y) -> x.start.compareTo(y.start));
        int bi = 0; double hi = -Double.MAX_VALUE, lo = Double.MAX_VALUE;   // экстремумы баров до первого дня композита
        for (Comp c : all) {
            long t0 = c.start.atStartOfDay(p.tz).toInstant().toEpochMilli();
            while (bi < res.bars.size() && res.bars.get(bi).t < t0) { Bar b = res.bars.get(bi++); hi = Math.max(hi, b.h); lo = Math.min(lo, b.l); }
            if (!prim.containsKey(c.start)) {
                double w = Math.max(c.hi - c.lo, p.tick);
                double out = Math.max(0, c.hi - Math.max(c.lo, hi)) + Math.max(0, Math.min(c.hi, lo) - c.lo);
                prim.put(c.start, hi > -Double.MAX_VALUE && out / w >= p.primaryShare - 1e-9);
            }
            c.primary = prim.get(c.start);
        }
        for (int i = 0; i < res.visible.size(); i++) {
            long o = res.sessions.get(i).ord;
            for (Shown v : res.visible.get(i)) {
                Zone z = v.z;
                if (z.k != K_SESS || z.ext || (z.minor && o < z.minorEnd)) continue;
                Comp cp = z.partOf;
                if (z.rcAt.length > 0) { int ri = lowerBound(z.rcAt, o + 1) - 1; if (ri >= 0) cp = z.rcComp[ri]; }
                if (cp == null || !cp.primary) continue;
                v.st.kind = z.nearCore || (z.hi >= cp.core.lo && z.lo <= cp.core.hi) ? Kind.CORE
                        : (z.lo + z.hi) / 2 > (cp.core.lo + cp.core.hi) / 2 ? Kind.HVN_R : Kind.HVN_S;
                z.primaryColor = true;
            }
        }
    }

    /** coreNearestBlue: для каждого баланса — его зоны (синхронизированы с ним или его снимками, либо родились в нём); если ни
     *  одна не задевает ядро, ближайшая к ядру становится синей (для этого композита: во всех его снимках). Пример — июнь 2024:
     *  ядро баланса 18.06–02.07 5531–5532.25 ни одна зона не задевала (узел 17.06 — в старом контракте), синей стала
     *  5533–5537 (узел 21.06, в 0.75 п. от ядра). */
    static void nearestToCoreBlue(List<Comp> balances, List<Zone> ses) {
        for (Comp c : balances) {
            Zone best = null; double bestGap = Double.MAX_VALUE; boolean touch = false;
            for (Zone e : ses) {
                if (e.ext) continue;                                // экстремум синим не становится и ядро не занимает
                boolean mine = e.partOf != null && e.partOf.start.equals(c.start);
                for (Comp rc : e.rcComp) if (rc.start.equals(c.start)) mine = true;
                if (!mine) continue;
                if (e.hi >= c.core.lo && e.lo <= c.core.hi) { touch = true; break; }
                double gap = Math.max(e.lo - c.core.hi, c.core.lo - e.hi);
                if (gap < bestGap) { bestGap = gap; best = e; }
            }
            if (touch || best == null) continue;
            for (int i = 0; i < best.rcComp.length; i++) if (best.rcComp[i].start.equals(c.start)) best.rcKind[i] = Kind.CORE;
            if (best.partOf != null && best.partOf.start.equals(c.start)) best.partKind = Kind.CORE;
            if (best.rcKind.length > 0) best.recolorKind = best.rcKind[0];
            best.nearCore = true;
        }
    }

    /** Узкая — серая (oneSquareGrey): сессионная зона не шире oneSquareMaxPts на графике (после раскладки), кроме синей
     *  (gridNarrowKeepBlue). */
    static void narrowToGrey(Result res, Params p) {
        for (List<Shown> l : res.visible) for (Shown v : l) {
            if (v.z.k != K_SESS || v.z.ext || v.hi - v.lo > p.oneSquareMaxPts + 1e-9 || v.st.kind == Kind.GREY) continue;
            if (p.gridNarrowKeepBlue && v.st.kind == Kind.CORE) continue;
            v.st.kind = Kind.GREY; v.z.oneSquare = true;
        }
    }

    /** Колеблющаяся в своём композите — серая (compRejGrey): по сессиям, композит — тот, что задал зоне цвет к этой сессии
     *  (синхронизация или композит рождения), отторжения на RTH-барах в его днях с появления зоны и до вчера. */
    static void compRejToGrey(Result res, Params p) {
        for (int i = 0; i < res.visible.size(); i++) {
            long o = res.sessions.get(i).ord;
            for (Shown v : res.visible.get(i)) {
                if (v.st.kind != Kind.HVN_R && v.st.kind != Kind.HVN_S || v.z.ext) continue;
                Zone z = v.z; Comp cp = z.partOf;
                if (z.rcAt.length > 0) { int ri = lowerBound(z.rcAt, o + 1) - 1; if (ri >= 0) cp = z.rcComp[ri]; }
                if (cp == null) continue;                                   // без композита — цвет дня рождения
                long a0 = Math.max(cp.start.toEpochDay(), z.av.toEpochDay()), a1 = Math.min(cp.end.toEpochDay(), o - 1);
                int nl = 0, ns = 0;
                for (long d : z.L) if (d >= a0 && d <= a1) nl++;
                for (long d : z.S) if (d >= a0 && d <= a1) ns++;
                int pro = v.st.kind == Kind.HVN_R ? ns : nl, con = v.st.kind == Kind.HVN_R ? nl : ns;
                if (nl + ns >= p.compRejMin && pro < p.compRejRatio * con - 1e-9) { v.st.kind = Kind.GREY; z.wavering = true; }
            }
        }
    }

    /** Стык «зелёные над красными» (в ценах графика, по сессиям): зоны по цене, подряд одного цвета — кластер (серые и синие
     *  разрывают); если над кластером красных сразу кластер зелёных и щель между ними ≤ invertGapPts, верхняя красная и нижняя
     *  зелёная зоны стыка — серые. Обычно это роли от разных композитов: нижние красные — над ядром старого, верхние зелёные —
     *  под ядром другого. */
    static void invertedToGrey(Result res, Params p) {
        for (List<Shown> l0 : res.visible) {
            List<Shown> l = new ArrayList<>(l0);
            l.removeIf(v -> v.z.ext);                                // экстремумы — не соседи: обычные зоны их не видят
            Map<Shown, Double> sh = new HashMap<>();
            for (Shown v : l) sh.put(v, rollShift(res, v.z.av.atStartOfDay(p.tz).toInstant().toEpochMilli()));
            l.sort((x, y) -> Double.compare(x.lo - sh.get(x), y.lo - sh.get(y)));
            List<Shown> hit = new ArrayList<>();
            for (int i = 0; i + 1 < l.size(); i++) {
                Shown a = l.get(i), b = l.get(i + 1);
                if (a.st.kind != Kind.HVN_R || b.st.kind != Kind.HVN_S) continue;   // соседи по цене: красная, над ней зелёная
                if ((b.lo - sh.get(b)) - (a.hi - sh.get(a)) > p.invertGapPts + 1e-9) continue;
                hit.add(a); hit.add(b);
            }
            for (Shown v : hit) if (!v.z.ext) { v.st.kind = Kind.GREY; v.z.inverted = true; }
        }
    }

    /** Края зоны (цены графика) — на границы квадратов TPO (gridPts, сдвиг gridOffsetPts): каждый край — к ближайшей границе,
     *  край посередине квадрата — наружу (квадрат с принтами не режется, а входит целиком). Зона уже квадрата — один квадрат,
     *  в котором больше её середина. */
    static double[] snapGrid(double lo, double hi, Params p) {
        double g = p.gridPts, o = p.gridOffsetPts, eps = 1e-9;
        double fl = (lo - o) / g, L = Math.floor(fl + eps), fh = (hi - o) / g, H = Math.floor(fh + eps);
        double nLo = (fl - L <= 0.5 + eps ? L : L + 1) * g + o;
        double nHi = (fh - H >= 0.5 - eps ? H + 1 : H) * g + o;
        if (nHi - nLo < g - eps) { double c = Math.floor(((lo + hi) / 2 - o) / g + eps); nLo = c * g + o; nHi = nLo + g; }
        return new double[]{nLo, nHi};
    }

    /** Раскладка в ценах графика: зона рисуется одной полосой в ценах контракта сессии появления (ZonePlan), поэтому
     *  зоны разных контрактов, не пересекающиеся в склеенных ценах, на графике могут лечь друг на друга. Зоны по
     *  порядку появления (старшая — главная; в один день — ядро, остальные, серые): младшая, которая в общие с уже
     *  уложенной сессии стоит к ней ближе sessMinGapPts (или налезает), сужается до самого широкого свободного куска
     *  (отступ sessMinGapPts), а уже minPiecePts — не рисуется. */
    static void chartLayout(Result res, Params p) {
        int n = res.visible.size();
        Map<Zone, List<Shown>> rows = new HashMap<>();
        Map<Zone, java.util.BitSet> when = new HashMap<>();
        Map<Zone, Shown> lastOf = new HashMap<>();
        List<Zone> order = new ArrayList<>();
        for (int i = 0; i < n; i++)
            for (Shown v : res.visible.get(i)) {
                if (!rows.containsKey(v.z)) { order.add(v.z); rows.put(v.z, new ArrayList<>()); when.put(v.z, new java.util.BitSet(n)); }
                rows.get(v.z).add(v); when.get(v.z).set(i); lastOf.put(v.z, v);
            }
        order.sort((x, y) -> {
            if (x.ext != y.ext) return x.ext ? 1 : -1;               // зоны-экстремумы — после обычных: обычные не трогают
            int c = x.av.compareTo(y.av);
            if (c != 0) return c;
            int rx = rank(lastOf.get(x).st.kind), ry = rank(lastOf.get(y).st.kind);
            return rx != ry ? rx - ry : Double.compare(x.blo, y.blo);
        });
        double g = p.sessMinGapPts, eps = 1e-9;
        List<double[]> placed = new ArrayList<>();              // {lo, hi} в ценах графика
        List<java.util.BitSet> placedWhen = new ArrayList<>();
        Set<Shown> drop = new HashSet<>();
        for (Zone z : order) {
            double sh = rollShift(res, z.av.atStartOfDay(p.tz).toInstant().toEpochMilli());
            Shown last = lastOf.get(z);
            double lo = last.lo - sh, hi = last.hi - sh;            // полоса, которую рисует ZonePlan (drawOneColor)
            java.util.BitSet w = when.get(z);
            List<double[]> pieces = new ArrayList<>(); pieces.add(new double[]{lo, hi});
            for (int k = 0; k < placed.size(); k++) {
                double[] u = placed.get(k);
                if (!placedWhen.get(k).intersects(w)) continue;
                if (Math.max(u[0], lo) - Math.min(u[1], hi) >= g - eps) continue;   // не ближе зазора — не мешает
                double ulo = u[0] - g, uhi = u[1] + g;
                List<double[]> next = new ArrayList<>();
                for (double[] q : pieces) {
                    if (uhi <= q[0] || ulo >= q[1]) { next.add(q); continue; }
                    if (ulo > q[0]) next.add(new double[]{q[0], ulo});
                    if (uhi < q[1]) next.add(new double[]{uhi, q[1]});
                }
                pieces = next;
            }
            double bLo = 0, bHi = -1;
            for (double[] q : pieces) if (q[1] - q[0] > bHi - bLo) { bLo = q[0]; bHi = q[1]; }
            double q = p.gridPts > 0 ? p.gridPts : p.tick, qo = p.gridPts > 0 ? p.gridOffsetPts : 0;   // внутрь: на квадраты (или тики)
            bLo = Math.ceil((bLo - qo) / q - eps) * q + qo; bHi = Math.floor((bHi - qo) / q + eps) * q + qo;
            if (bHi - bLo < p.minPiecePts - eps) { drop.addAll(rows.get(z)); continue; }
            for (Shown v : rows.get(z)) {
                v.lo = Math.max(v.lo, bLo + sh); v.hi = Math.min(v.hi, bHi + sh);
                if (v.hi - v.lo < p.minPiecePts - eps) drop.add(v);
            }
            placed.add(new double[]{bLo, bHi}); placedWhen.add(w);
        }
        if (drop.isEmpty()) return;
        for (List<Shown> l : res.visible) l.removeIf(drop::contains);
    }

    /** Раскладка без пересечений: ядра, затем остальные зоны (старшие первыми), затем серые; зона,
     *  налезающая на уже уложенные, обрезается до самого широкого свободного куска (≥ minPiecePts). */
    static List<Shown> layout(List<Shown> in, Params p) {
        List<Shown> order = new ArrayList<>(in);
        order.sort((x, y) -> {
            if (x.z.ext != y.z.ext) return x.z.ext ? 1 : -1;     // зоны-экстремумы — после обычных: обычные не трогают
            int rx = rank(x.st.kind), ry = rank(y.st.kind);
            if (rx != ry) return rx - ry;
            int c = x.z.av.compareTo(y.z.av);
            return c != 0 ? c : Double.compare(x.z.blo, y.z.blo);
        });
        List<double[]> used = new ArrayList<>();
        List<Shown> out = new ArrayList<>();
        for (Shown v : order) {
            double bestLo = 0, bestHi = -1;
            List<double[]> pieces = new ArrayList<>(); pieces.add(new double[]{v.z.blo, v.z.bhi});
            for (double[] u : used) {
                List<double[]> next = new ArrayList<>();
                for (double[] q : pieces) {
                    if (u[1] <= q[0] || u[0] >= q[1]) { next.add(q); continue; }
                    if (u[0] > q[0]) next.add(new double[]{q[0], u[0]});
                    if (u[1] < q[1]) next.add(new double[]{u[1], q[1]});
                }
                pieces = next;
            }
            for (double[] q : pieces) if (q[1] - q[0] > bestHi - bestLo) { bestLo = q[0]; bestHi = q[1]; }
            if (p.layoutDropYounger && (bestLo != v.z.blo || bestHi != v.z.bhi)) continue;   // налезает — не рисуется
            if (bestHi - bestLo < p.minPiecePts - 1e-9) continue;
            v.lo = bestLo; v.hi = bestHi;
            used.add(new double[]{bestLo, bestHi});
            out.add(v);
        }
        return out;
    }

    private static int rank(Kind k) { return k == Kind.CORE ? 0 : k == Kind.GREY ? 2 : 1; }

    // ------------------------------------------------------------------ роллы
    /** Склейка роллов: в окне 10 дней до 3-й пятницы марта / июня / сентября / декабря — самый большой скачок между
     *  соседними (через periodMinutes) барами, если он не меньше rollMinGapPts, — смена контракта. Цены всех баров до
     *  ролла сдвигаются на его гэп (аддитивно, как tpo/rolls.py, но гэп фактический, а не по модели carry). */
    static List<Bar> rollAdjust(List<Bar> bars, Params p, Result res) {
        long step = p.periodMinutes * 60_000L;
        Map<LocalDate, int[]> best = new HashMap<>();          // 3-я пятница → {индекс бара, }
        Map<LocalDate, Double> bestGap = new HashMap<>();
        for (int i = 1; i < bars.size(); i++) {
            Bar b = bars.get(i), a = bars.get(i - 1);
            if (b.t - a.t != step) continue;
            LocalDate d = LocalDateTime.ofInstant(Instant.ofEpochMilli(b.t), p.tz).toLocalDate();
            int m = d.getMonthValue();
            if (m % 3 != 0) continue;
            LocalDate f = d.withDayOfMonth(1);
            while (f.getDayOfWeek() != java.time.DayOfWeek.FRIDAY) f = f.plusDays(1);
            f = f.plusWeeks(2);                                  // 3-я пятница
            if (d.isAfter(f) || d.isBefore(f.minusDays(10))) continue;
            double g = b.o - a.c;
            if (Math.abs(g) < p.rollMinGapPts - 1e-9) continue;
            if (!bestGap.containsKey(f) || Math.abs(g) > Math.abs(bestGap.get(f))) { bestGap.put(f, g); best.put(f, new int[]{i}); }
        }
        if (best.isEmpty()) return bars;
        res.rawPrices = p.rollRaw;
        List<LocalDate> keys = new ArrayList<>(best.keySet());
        keys.sort(null);
        res.rollMs = new long[keys.size()]; res.rollGap = new double[keys.size()];
        for (int k = 0; k < keys.size(); k++) {
            res.rollMs[k] = bars.get(best.get(keys.get(k))[0]).t;
            res.rollGap[k] = Math.rint(bestGap.get(keys.get(k)) / p.tick) * p.tick;
        }
        if (res.rawPrices) return bars;                         // роллы найдены, цены — как на графике
        List<Bar> out = new ArrayList<>(bars.size());
        for (Bar b : bars) {
            double sh = rollShift(res, b.t);
            out.add(sh == 0 ? b : new Bar(b.t, b.o + sh, b.h + sh, b.l + sh, b.c + sh));
        }
        return out;
    }

    /** Ролл после RTH дня a и не позже дня b (a < b): смена контракта в 15:00 CT дня ролла — после его RTH. */
    static boolean rollBetween(Result res, Params p, LocalDate a, LocalDate b) {
        for (long t : res.rollMs) {
            LocalDate r = LocalDateTime.ofInstant(Instant.ofEpochMilli(t), p.tz).toLocalDate();
            if (!r.isBefore(a) && r.isBefore(b)) return true;
        }
        return false;
    }

    /** Сдвиг цен бара, начавшегося в t, к текущему контракту: сумма гэпов роллов после t. */
    public static double rollShift(Result res, long t) {
        double s = 0;
        if (res.rawPrices) return 0;                             // расчёт в ценах графика — сдвига нет
        for (int k = 0; k < res.rollMs.length; k++) if (res.rollMs[k] > t) s += res.rollGap[k];
        return s;
    }

    // ------------------------------------------------------------------ композиты
    /** compVaPrevDay: VA дня пересекается с VA последнего полного дня кандидата на ≥ compVaOverlap меньшей из двух. */
    /** Композиты по стоимости (compValue): см. Params. */
    static void valueComposites(List<Day> days, Result res, Params p, List<Zone> ob, List<Zone> greyComp,
                                Map<LocalDate, LocalDate> nextTradeDay) {
        List<Day> cand = new ArrayList<>(); Profile cp = null; List<Day> miss = new ArrayList<>();
        for (Day d : days) {
            if (!cand.isEmpty() && p.compRollBreak && rollBetween(res, p, cand.get(cand.size() - 1).date, d.date)) {
                closeComposite(cand, cp, miss.isEmpty() ? d.date : miss.get(0).date, p, ob, greyComp, res.comps, res.notBalance);
                cand = new ArrayList<>(); cp = null; miss.clear();
            }
            boolean smear = p.smearMaxTpo > 0 && !d.shortDay && d.maxSq <= p.smearMaxTpo;
            boolean wide = p.compDayMaxAdr > 0 && !Double.isNaN(d.adr) && d.high - d.low > p.compDayMaxAdr * d.adr + 1e-9;
            if (smear || wide) res.smearDays++;
            boolean eligible = !smear && !wide;
            if (cand.isEmpty()) { if (eligible) { cand.add(d); cp = d.profile; } continue; }
            if (eligible && valueIn(d, cp, p) && !tooWide(cp, d, p)) {
                miss.clear(); cand.add(d); cp = merge(cp, d.profile);
                live(cand, cp, nextTradeDay.get(d.date), p, res.liveComps);
                continue;
            }
            miss.add(d);
            if (miss.size() <= p.compExcursion) continue;
            closeComposite(cand, cp, miss.get(0).date, p, ob, greyComp, res.comps, res.notBalance);
            // новый кандидат — с последнего подходящего дня из пропущенных (и дальше по ним)
            cand = new ArrayList<>(); cp = null;
            for (Day x : miss) {
                boolean ok = !(p.smearMaxTpo > 0 && !x.shortDay && x.maxSq <= p.smearMaxTpo)
                        && !(p.compDayMaxAdr > 0 && !Double.isNaN(x.adr) && x.high - x.low > p.compDayMaxAdr * x.adr + 1e-9);
                if (!ok) { cand = new ArrayList<>(); cp = null; continue; }
                if (cand.isEmpty() || !(valueIn(x, cp, p) && !tooWide(cp, x, p))) { cand = new ArrayList<>(); cand.add(x); cp = x.profile; }
                else { cand.add(x); cp = merge(cp, x.profile); }
            }
            miss.clear();
        }
    }

    /** Стоимость дня в стоимости композита: POC дня внутри VA композита или VA дня на ≥ compValueShare внутри. */
    static boolean valueIn(Day d, Profile cp, Params p) {
        int[] cva = valueArea(cp, p.valueAreaPct);
        if (d.poc >= cva[1] && d.poc <= cva[2]) return true;
        int inter = Math.min(d.vah, cva[2]) - Math.max(d.val, cva[1]) + 1;
        return inter > 0 && inter / (double) (d.vah - d.val + 1) >= p.compValueShare - 1e-9;
    }

    /** Композит с этим днём шире compMaxAdr средних дневных диапазонов. */
    static boolean tooWide(Profile cp, Day d, Params p) {
        if (p.compMaxAdr <= 0 || Double.isNaN(d.adr)) return false;
        double w = (Math.max(cp.high(), ticks(d.high, p.tick)) - Math.min(cp.low, ticks(d.low, p.tick))) * p.tick;
        return w > p.compMaxAdr * d.adr + 1e-9;
    }

    static boolean vaPrev(Day d, List<Day> cand, Params p) {
        if (!p.compVaPrevDay || p.compVaOverlap <= 0 || cand == null) return false;
        Day y = null;
        for (int i = cand.size() - 1; i >= 0 && y == null; i--) if (!cand.get(i).shortDay) y = cand.get(i);
        if (y == null) return false;
        int inter = Math.min(d.vah, y.vah) - Math.max(d.val, y.val) + 1;
        int narrow = Math.min(d.vah - d.val, y.vah - y.val) + 1;
        return inter > 0 && inter / (double) narrow >= p.compVaOverlap;
    }

    /** VA дня и VA композита пересекаются на ≥ compVaOverlap меньшей из двух (compVaOverlap = 0 — проверка выключена). */
    static boolean vaJoins(Day d, Profile cp, Params p) {
        if (p.compVaOverlap <= 0) return true;
        int[] cva = valueArea(cp, p.valueAreaPct);
        int inter = Math.min(d.vah, cva[2]) - Math.max(d.val, cva[1]) + 1;
        int narrow = Math.min(d.vah - d.val, cva[2] - cva[1]) + 1;
        return inter > 0 && inter / (double) narrow >= p.compVaOverlap;
    }

    /** Внутренний день: High–Low дня на ≥ compInsideShare внутри диапазона композита; внешний (compOutsideDays) —
     *  наоборот, диапазон композита на ≥ эту долю внутри High–Low дня (compInsideShare = 0 — выкл.). */
    static boolean insideDay(Day d, Profile cp, Params p) {
        if (p.compInsideShare <= 0) return false;
        int lo = ticks(d.low, p.tick), hi = ticks(d.high, p.tick);
        int in = Math.min(hi, cp.high()) - Math.max(lo, cp.low);
        if (in < 0) return false;
        int w = p.compOutsideDays ? Math.min(hi - lo, cp.high() - cp.low) : hi - lo;   // доля от более узкого
        return w == 0 || in / (double) w >= p.compInsideShare - 1e-9;
    }

    /** Снимок формирующегося композита после дня: кандидат ≥ compositeMinDays дней — композит со следующей сессии av
     *  (ядро, тело — по уже прошедшим дням). Не баланс — снимка нет. */
    private static void live(List<Day> cand, Profile cp, LocalDate av, Params p, List<Comp> out) {
        if (!p.compositeMode || !p.liveComposite || av == null) return;
        int fullDays = 0; for (Day d : cand) if (!d.shortDay) fullDays++;
        if (fullDays < p.compositeMinDays) return;                // короткие дни — в профиле, но не в счёте дней
        List<Comp> comps = new ArrayList<>();
        closeComposite(new ArrayList<>(cand), cp, av, p, new ArrayList<>(), new ArrayList<>(), comps, new ArrayList<>());
        for (Comp c : comps) { c.live = true; out.add(c); }
    }

    private static Comp closeComposite(List<Day> cand, Profile cp, LocalDate endDay, Params p,
                                       List<Zone> ob, List<Zone> greyComp, List<Comp> comps, List<Comp> notBalance) {
        int n = cand.size();
        Comp made = null;
        if (n < p.greyCompositeMinDays) return null;
        int[] va = valueArea(cp, p.valueAreaPct);
        if (n >= p.compositeMinDays) {
            Comp c = new Comp();
            made = c; c.prof = cp; c.pocT = va[0];
            c.lo = cp.low * p.tick; c.hi = cp.high() * p.tick; c.av = endDay; c.days = n;
            c.start = cand.get(0).date; c.end = cand.get(n - 1).date;
            for (Day x : cand) c.dates.add(x.date);
            Zone core = new Zone();
            core.k = K_CORE; core.av = endDay; core.src = "core";
            core.info = coreInfo(cand, cp, va[0], p.tick);
            int[] band = p.coreByVa ? new int[]{va[1], va[2]} : hvnBand(cp, va[0], p.hvnShare);
            int poc = va[0];
            int cap = (int) Math.round(p.zoneMaxWidthPts * vs(p, cand) / p.tick);
            if (p.coreFromSessions && !p.coreByVa) {
                // ядро — от POC композита до обрыва TPO (и не ниже hvnShare максимума), не шире лимита;
                // композит — баланс, если VA его дней задевают ядро
                band = narrow(cp, cliffBand(cp, poc, p.hvnShare, p.coreCliffTpo, 4), poc, cap);
                int hit = 0;
                for (Day d : cand) if (d.val <= band[1] && d.vah >= band[0]) hit++;
                if (p.coreMinDaysShare > 0 && hit < Math.ceil(p.coreMinDaysShare * n - 1e-9)) {
                    band = null;                // не баланс: дни композита не сходятся в одной зоне
                }
            } else band = narrow(cp, band, poc, cap);
            if (band == null) notBalance.add(c);
            else {
                core.lo = band[0] * p.tick; core.hi = band[1] * p.tick;
                c.core = core;
                comps.add(c);
                if (p.partition || p.sessionZones) {      // тело и ядро тела: по телу сессионные зоны получают роль
                    int[] body = new int[4];
                    c.segs = partition(cand, cp, poc, p, body);
                    c.bodyLo = body[0] * p.tick; c.bodyHi = body[1] * p.tick;
                    c.coreLo = body[2] * p.tick; c.coreHi = body[3] * p.tick;
                }
                if (p.coreNodes && !p.partition) {
                    List<int[]> nd = nodes(cp, poc, p.hvnShare, p.nodeDipTpo, p.nodeDropTpo, (int) Math.round(1.0 / p.tick));
                    if (!nd.isEmpty()) {
                        List<Zone> zs = new ArrayList<>();
                        int lo = Integer.MAX_VALUE, hi = Integer.MIN_VALUE;
                        for (int[] q : nd) {
                            Zone nz = new Zone();
                            nz.k = K_CORE; nz.av = endDay; nz.src = "node"; nz.info = core.info; nz.nodeOf = core;
                            nz.lo = q[0] * p.tick; nz.hi = q[1] * p.tick;
                            zs.add(nz); lo = Math.min(lo, q[0]); hi = Math.max(hi, q[1]);
                        }
                        core.nodes = zs;
                        core.lo = lo * p.tick; core.hi = hi * p.tick;      // ядро для классов — от нижнего до верхнего узла
                    }
                }
            }
        }
        Zone z = new Zone();
        z.av = endDay; z.src = "comp";
        if (n >= p.compositeMinDays) {
            z.k = K_OLD_BALANCE; z.lo = va[1] * p.tick; z.hi = va[2] * p.tick;
            ob.add(z);
        } else {
            int[] band = hvnBand(cp, va[0], p.hvnShare);
            z.k = K_GREY; z.lo = band[0] * p.tick; z.hi = band[1] * p.tick;
            greyComp.add(z);
        }
        return made;
    }

    /** Роль цены mid в композите: ядро — проторговка, выше — сопротивление, ниже — поддержка. */
    /** Роль сессионной зоны в композите: sessCoreTouch — синяя, если зона задевает ядро композита (полоса у POC до обрыва,
     *  ≤ 10 п.), иначе выше / ниже его середины — сопротивление / поддержка; без него — по ядру тела (roleIn). */
    static Kind roleOf(Comp c, Zone z, Params p) {
        double mid = (z.lo + z.hi) / 2;
        if (!p.sessCoreTouch) return roleIn(c, mid);
        if (z.hi >= c.core.lo && z.lo <= c.core.hi) return Kind.CORE;
        return mid > (c.core.lo + c.core.hi) / 2 ? Kind.HVN_R : Kind.HVN_S;
    }

    static Kind roleIn(Comp c, double mid) {
        return mid > c.coreHi ? Kind.HVN_R : mid < c.coreLo ? Kind.HVN_S : Kind.CORE;
    }

    /** Роль зоны вне композита по сессии её рождения: отторжения (первичны), иначе — где закрылась сессия, иначе серая. */
    static Kind roleByDay(Day d, double lo, double hi, double mw, int mode) {
        int nl = 0, ns = 0;
        for (double[] b : d.sbars) {
            int t = interaction(b[0], b[1], b[2], b[3], lo, hi, mw);
            if (t == 1) nl++; else if (t == 2) ns++;
        }
        if (mode == 0 && nl > ns) return Kind.HVN_S;
        if (mode == 0 && ns > nl) return Kind.HVN_R;
        double close = d.sbars.isEmpty() ? d.close : d.sbars.get(d.sbars.size() - 1)[3];
        return close > hi ? Kind.HVN_S : close < lo ? Kind.HVN_R : Kind.GREY;
    }

    /** Узлы RTH TPO-профиля дня (сглаж. 1 п.): вершины ≥ sessMinPeak, разделённые провалами; узел — строки не ниже
     *  вершины на sessNodeDrop; затем подгонка по телам свечей сессии в пределах узла ± 1 п. (скопление ≥ 70% максимума).
     *  Ширина — от 1 п. до sessMaxPts. {lo, hi} в тиках. */
    static List<int[]> dayNodes(Day d, Params p, int onePt, int maxW) {
        Profile pf = d.profile; int[] c = pf.counts; int n = c.length, half = onePt / 2;
        int sq = p.gridPts > 0 ? (int) Math.round(p.gridPts / p.tick) : onePt;   // квадрат TPO в тиках
        double[] sm = new double[n];
        for (int i = 0; i < n; i++) {
            double sum = 0; int k = 0;
            for (int j = Math.max(0, i - half); j <= Math.min(n - 1, i + half); j++) { sum += c[j]; k++; }
            sm[i] = sum / k;
        }
        double dayMax = 0; for (double v : sm) dayMax = Math.max(dayMax, v);
        List<int[]> pcs = new ArrayList<>();
        splitRel(sm, 0, n - 1, p.sessNodeDip, 1.0, pcs);
        int[] occ = new int[n + 2 * onePt + 1];               // тела свечей сессии, со сдвигом onePt
        for (int[] q : d.bodies) {
            int x0 = Math.min(q[0], q[1]) - pf.low + onePt, x1 = Math.max(q[0], q[1]) - pf.low + onePt;
            x0 = Math.max(0, x0); x1 = Math.min(occ.length - 2, x1);
            if (x0 <= x1) { occ[x0]++; occ[x1 + 1]--; }
        }
        for (int i = 1; i < occ.length; i++) occ[i] += occ[i - 1];
        List<int[]> out = new ArrayList<>();
        for (int[] q : pcs) {
            int m = q[0];
            for (int i = q[0]; i <= q[1]; i++) if (sm[i] > sm[m]) m = i;
            int weak = (p.sessPeakShare > 0 && sm[m] < p.sessPeakShare * dayMax)
                    || (c[m] < p.sessMinPeak && sm[m] < p.sessMinPeak) ? 1 : 0;           // слабый узел — минорная зона
            if (weak == 1 && (!p.sessMinor || (c[m] < p.sessMinorMinPeak && sm[m] < p.sessMinorMinPeak))) continue;
            int lo = m, hi = m;
            while (lo > q[0] && sm[lo - 1] >= sm[m] - p.sessNodeDrop) lo--;
            while (hi < q[1] && sm[hi + 1] >= sm[m] - p.sessNodeDrop) hi++;
            // подгонка по телам: скопление тел в узле ± 1 п.
            int w0 = lo, w1 = hi + 2 * onePt, bm = w0;              // индексы в occ (со сдвигом onePt)
            for (int i = w0; i <= w1; i++) if (occ[i] > occ[bm]) bm = i;
            if (occ[bm] >= 2) {
                double thr = p.sessBodyShare * occ[bm];
                int x0 = bm, x1 = bm;
                while (x0 > w0 && occ[x0 - 1] >= thr) x0--;
                while (x1 < w1 && occ[x1 + 1] >= thr) x1++;
                lo = x0 - onePt; hi = x1 - onePt;
            }
            if (p.sessRefine) {                                    // мнимая зона пошире, внутри — все скопления тел
                int plo = m, phi = m;
                while (plo > q[0] && sm[plo - 1] >= p.sessParentShare * sm[m]) plo--;
                while (phi < q[1] && sm[phi + 1] >= p.sessParentShare * sm[m]) phi++;
                int v0 = Math.max(0, plo), v1 = Math.min(occ.length - 1, phi + 2 * onePt);   // ± 1 п. (occ со сдвигом onePt)
                double[] od = new double[occ.length];
                for (int i = 0; i < occ.length; i++) od[i] = occ[i];
                double omax = 0; for (int i = v0; i <= v1; i++) omax = Math.max(omax, od[i]);
                List<int[]> sub = new ArrayList<>();
                splitRel(od, v0, v1, p.sessClusterDip, 1.5, sub);
                int added = 0;
                for (int[] u : sub) {
                    int b = u[0]; for (int i = u[0]; i <= u[1]; i++) if (od[i] > od[b]) b = i;
                    if (od[b] < 2 || od[b] < p.sessClusterMin * omax) continue;
                    double thr = p.sessBodyShare * od[b];
                    int x0 = b, x1 = b;
                    while (x0 > u[0] && od[x0 - 1] >= thr) x0--;
                    while (x1 < u[1] && od[x1 + 1] >= thr) x1++;
                    int zl = x0 - onePt, zh = x1 - onePt;
                    int nw = zh - zl < sq ? 1 : 0;                   // скопление тел уже одного квадрата
                    if (zh - zl < onePt) { int mid = (zl + zh) / 2; zl = mid - onePt / 2; zh = zl + onePt; }
                    if (zh - zl > maxW) { int s0 = p.densestCrop ? densest(occ, zl + onePt, zh + onePt, maxW) - onePt : (zl + zh) / 2 - maxW / 2; zl = s0; zh = zl + maxW; }
                    out.add(new int[]{pf.low + zl, pf.low + zh, q[0], weak, nw}); added++;
                }
                if (added > 0) continue;                           // тел нет — как раньше, узел целиком
            }
            int nw = hi - lo < sq ? 1 : 0;
            if (hi - lo < onePt) { int mid = (lo + hi) / 2; lo = mid - onePt / 2; hi = lo + onePt; }
            if (hi - lo > maxW) { int s0 = p.densestCrop ? densest(occ, lo + onePt, hi + onePt, maxW) - onePt : (lo + hi) / 2 - maxW / 2; lo = s0; hi = lo + maxW; }
            out.add(new int[]{pf.low + lo, pf.low + hi, q[0], weak, nw});
        }
        out.sort((x, y) -> Integer.compare(x[3], y[3]));       // основные узлы первыми — место у них, минорные потом
        return out;
    }

    /** Разметка тела композита. Тело — строки от POC, пока сглаженный (1 п.) счёт не ниже bodyShare
     *  максимума; ядро — часть тела не ниже partCoreShare. Ниже ядра — поддержка, выше — сопротивление.
     *  Каждая часть режется по провалам (глубина ≥ max(2, partDipShare × максимум) от обеих сторон), кусок
     *  шире partMaxPts — на равные части ≈ partTargetPts (границы — к краям HVN-полос дней в пределах 2 п.),
     *  куски уже partMinPts присоединяются к соседу. body — {низ, верх} тела в тиках. */
    static List<int[]> partition(List<Day> days, Profile cp, int pocTicks, Params p, int[] body) {
        int[] c = cp.counts; int n = c.length, onePt = (int) Math.round(1.0 / p.tick), half = onePt / 2;
        double[] sm = new double[n];
        for (int i = 0; i < n; i++) {
            double sum = 0; int k = 0;
            for (int j = Math.max(0, i - half); j <= Math.min(n - 1, i + half); j++) { sum += c[j]; k++; }
            sm[i] = sum / k;
        }
        double mx = 0; for (double v : sm) mx = Math.max(mx, v);
        int pi = pocTicks - cp.low, a = pi, b = pi, ca = pi, cb = pi;
        int maxBody = (int) Math.round(p.partMaxBodyPts / p.tick);
        for (double share = p.bodyShare; ; share += 0.05) {
            a = pi; b = pi;
            while (a > 0 && sm[a - 1] >= share * mx) a--;
            while (b < n - 1 && sm[b + 1] >= share * mx) b++;
            if (p.partMaxBodyPts <= 0 || b - a <= maxBody || share >= p.partCoreShare) break;
        }
        while (ca > a && sm[ca - 1] >= p.partCoreShare * mx) ca--;
        while (cb < b && sm[cb + 1] >= p.partCoreShare * mx) cb++;
        body[0] = cp.low + a; body[1] = cp.low + b;
        if (body.length > 3) { body[2] = cp.low + ca; body[3] = cp.low + cb; }
        double dip = Math.max(2.0, p.partDipShare * mx);
        // профиль тел: сколько тел 30m-свечей (open–close) дней композита перекрывает каждую строку
        int[] occ = new int[n + 1];
        for (Day d : days) for (int[] q0 : p.fitEthBodies ? d.bodies : rthBodies(d)) {
            int[] q = {0, 0, 0, q0[0], q0[1]};
            int x0 = Math.max(0, Math.min(q[3], q[4]) - cp.low), x1 = Math.min(n - 1, Math.max(q[3], q[4]) - cp.low);
            if (x0 <= x1) { occ[x0]++; occ[x1 + 1]--; }
        }
        for (int i = 1; i <= n; i++) occ[i] += occ[i - 1];
        int occMax = 0; for (int i = a; i <= b; i++) occMax = Math.max(occMax, occ[i]);
        double bodyThr = Math.max(3, p.fitBodyShare * occMax);
        int maxW = (int) Math.round(p.partMaxPts / p.tick), tgt = (int) Math.round(p.partTargetPts / p.tick),
            minW = (int) Math.round(p.partMinPts / p.tick);
        List<Integer> edges = new ArrayList<>();
        for (Day d : days) if (d.bandHi > 0) { edges.add(d.bandLo - cp.low); edges.add(d.bandHi - cp.low + 1); }
        List<int[]> out = new ArrayList<>();
        int[][] regions = {{a, ca - 1, 1}, {ca, cb, 0}, {cb + 1, b, 2}};
        if (p.bodyClusters) {
            // профиль тел, сглаженный до 1 п.; скопления — по провалам; зона — тела вокруг вершины скопления
            double[] os = new double[n];
            for (int i = 0; i < n; i++) {
                double sum = 0; int k = 0;
                for (int j = Math.max(0, i - half); j <= Math.min(n - 1, i + half); j++) { sum += occ[j]; k++; }
                os[i] = sum / k;
            }
            for (int[] r : regions) {
                if (r[1] < r[0]) continue;
                List<int[]> pcs = new ArrayList<>();
                splitRel(os, r[0], r[1], p.clusterDip, 1.5, pcs);
                for (int[] q : pcs) {
                    int m = q[0];
                    for (int i = q[0]; i <= q[1]; i++) if (os[i] > os[m]) m = i;
                    if (os[m] < bodyThr) continue;                  // тела здесь не скапливаются
                    int x0 = m, x1 = m;
                    for (double sh = p.fitClusterShare; ; sh += 0.05) {
                        double thr = Math.max(bodyThr, sh * os[m]);
                        x0 = m; x1 = m;
                        while (x0 > q[0] && os[x0 - 1] >= thr) x0--;
                        while (x1 < q[1] && os[x1 + 1] >= thr) x1++;
                        if (x1 - x0 <= (int) Math.round(p.clusterMaxPts / p.tick) || sh >= 0.95) break;
                    }
                    if (x1 - x0 + 1 < minW) continue;
                    out.add(new int[]{cp.low + x0, cp.low + x1, r[2]});
                }
            }
            return out;
        }
        for (int[] r : regions) {
            if (r[1] < r[0]) continue;
            List<int[]> pcs = new ArrayList<>();
            splitDips(sm, r[0], r[1], dip, pcs);
            List<int[]> fine = new ArrayList<>();
            for (int[] q : pcs) {
                int w = q[1] - q[0] + 1;
                if (w <= maxW) { fine.add(q); continue; }
                int k = (int) Math.ceil(w / (double) tgt);
                int start = q[0];
                for (int j = 1; j < k; j++) {
                    int cut = q[0] + (int) Math.round(j * w / (double) k), best = cut;
                    for (int e : edges) if (e > start + minW && e < q[1] - minW && Math.abs(e - cut) <= 2 * onePt && Math.abs(e - cut) < Math.abs(best - cut)) best = e;
                    fine.add(new int[]{start, best - 1}); start = best;
                }
                fine.add(new int[]{start, q[1]});
            }
            // слишком узкие — к соседу поуже
            boolean ch = true;
            while (ch && fine.size() > 1) {
                ch = false;
                for (int i = 0; i < fine.size(); i++) {
                    int[] q = fine.get(i);
                    if (q[1] - q[0] + 1 >= minW) continue;
                    int j = i == 0 ? 1 : i == fine.size() - 1 ? i - 1
                            : (fine.get(i - 1)[1] - fine.get(i - 1)[0] <= fine.get(i + 1)[1] - fine.get(i + 1)[0] ? i - 1 : i + 1);
                    int[] m = fine.get(j);
                    fine.set(Math.min(i, j), new int[]{Math.min(q[0], m[0]), Math.max(q[1], m[1])});
                    fine.remove(Math.max(i, j)); ch = true; break;
                }
            }
            for (int[] q : fine) {
                int lo = q[0], hi = q[1];
                if (p.fitBodyShare > 0) {         // подгонка по телам: зона — скопление тел свечей в куске
                    int m = lo;
                    for (int i = lo; i <= hi; i++) if (occ[i] > occ[m]) m = i;
                    double thr = Math.max(bodyThr, p.fitClusterShare * occ[m]);
                    if (occ[m] < thr) continue;         // тела здесь не скапливаются — зоны нет
                    int x0 = m, x1 = m;
                    while (x0 > lo && occ[x0 - 1] >= thr) x0--;
                    while (x1 < hi && occ[x1 + 1] >= thr) x1++;
                    lo = x0; hi = x1;
                    if (hi - lo + 1 < minW) continue;
                }
                if (hi - lo + 1 >= Math.min(minW, onePt)) out.add(new int[]{cp.low + lo, cp.low + hi, r[2]});
            }
        }
        return out;
    }

    private static List<int[]> rthBodies(Day d) {
        List<int[]> out = new ArrayList<>();
        for (int[] q : d.periods) out.add(new int[]{q[3], q[4]});
        return out;
    }

    /** Деление [lo, hi] по провалам: глубина ≥ max(abs, rel × меньшая из вершин слева и справа). */
    private static void splitRel(double[] v, int lo, int hi, double rel, double abs, List<int[]> out) {
        int best = -1; double bestDepth = 0;
        for (int i = lo + 1; i < hi; i++) {
            if (!(v[i] <= v[i - 1] && v[i] <= v[i + 1])) continue;
            double l = 0, r = 0;
            for (int j = lo; j < i; j++) l = Math.max(l, v[j]);
            for (int j = i + 1; j <= hi; j++) r = Math.max(r, v[j]);
            double depth = Math.min(l, r) - v[i];
            if (depth >= Math.max(abs, rel * Math.min(l, r)) && depth > bestDepth) { bestDepth = depth; best = i; }
        }
        if (best < 0) { out.add(new int[]{lo, hi}); return; }
        splitRel(v, lo, best - 1, rel, abs, out);
        splitRel(v, best, hi, rel, abs, out);
    }

    private static void splitDips(double[] sm, int lo, int hi, double dip, List<int[]> out) {
        int best = -1; double bestDepth = 0;
        for (int i = lo + 1; i < hi; i++) {
            if (!(sm[i] <= sm[i - 1] && sm[i] <= sm[i + 1])) continue;
            double l = 0, r = 0;
            for (int j = lo; j < i; j++) l = Math.max(l, sm[j]);
            for (int j = i + 1; j <= hi; j++) r = Math.max(r, sm[j]);
            double depth = Math.min(l, r) - sm[i];
            if (depth >= dip && depth > bestDepth) { bestDepth = depth; best = i; }
        }
        if (best < 0) { out.add(new int[]{lo, hi}); return; }
        splitDips(sm, lo, best - 1, dip, out);
        splitDips(sm, best, hi, dip, out);
    }

    /** Подсказка ядра: дни композита, диапазон, POC и две самые высокие вершины профиля (вторая — за провалом вне плато POC). */
    static String coreInfo(List<Day> days, Profile cp, int poc, double tick) {
        StringBuilder b = new StringBuilder("композит ");
        for (int i = 0; i < days.size(); i++) b.append(i == 0 ? "" : ", ").append(days.get(i).date.toString().substring(5));
        b.append(String.format(Locale.ROOT, "; диапазон %.2f – %.2f; POC %.2f (%d TPO)",
                cp.low * tick, cp.high() * tick, poc * tick, cp.counts[poc - cp.low]));
        int[] c = cp.counts; int i1 = poc - cp.low, i2 = -1;
        // плато первой вершины: подряд строки не ниже её TPO − 2; вторая вершина — вне плато, через провал
        int a = i1, z = i1;
        while (a > 0 && c[a - 1] >= c[i1] - 2) a--;
        while (z < c.length - 1 && c[z + 1] >= c[i1] - 2) z++;
        for (int i = 0; i < c.length; i++) if ((i < a || i > z) && (i2 < 0 || c[i] > c[i2])) i2 = i;
        b.append(String.format(Locale.ROOT, "; вершины %.2f (%d)", (cp.low + i1) * tick, cp.counts[i1]));
        if (i2 >= 0) b.append(String.format(Locale.ROOT, ", %.2f (%d)", (cp.low + i2) * tick, cp.counts[i2]));
        return b.toString();
    }

    /** Узлы профиля: участок строк не ниже share × максимум (сглаживание 1 п.) вокруг POC делится по
     *  провалам глубиной ≥ dip TPO от обеих соседних вершин; узел — строки около вершины участка, не ниже
     *  её на drop TPO, не уже 1 п. {lo, hi} в тиках, снизу вверх. */
    static List<int[]> nodes(Profile p, int pocTicks, double share, double dip, double drop, int onePt) {
        int[] c = p.counts; int n = c.length, half = onePt / 2;
        double[] sm = new double[n];
        for (int i = 0; i < n; i++) {
            double sum = 0; int k = 0;
            for (int j = Math.max(0, i - half); j <= Math.min(n - 1, i + half); j++) { sum += c[j]; k++; }
            sm[i] = sum / k;
        }
        double mx = 0; for (double v : sm) mx = Math.max(mx, v);
        int pi = pocTicks - p.low, a = pi, b = pi;
        while (a > 0 && sm[a - 1] >= share * mx) a--;
        while (b < n - 1 && sm[b + 1] >= share * mx) b++;
        // локальные вершины и провалы внутри [a, b]
        List<Integer> peaks = new ArrayList<>();
        for (int i = a; i <= b; i++) {
            boolean up = i == a || sm[i] >= sm[i - 1], dn = i == b || sm[i] >= sm[i + 1];
            if (up && dn && (peaks.isEmpty() || i - peaks.get(peaks.size() - 1) > 1)) peaks.add(i);
        }
        // склейка вершин, между которыми провал мельче dip
        boolean merged = true;
        while (merged && peaks.size() > 1) {
            merged = false;
            for (int k = 0; k + 1 < peaks.size(); k++) {
                int x = peaks.get(k), y = peaks.get(k + 1);
                double low = Double.MAX_VALUE; for (int i = x; i <= y; i++) low = Math.min(low, sm[i]);
                if (Math.min(sm[x], sm[y]) - low < dip) { peaks.remove(sm[x] >= sm[y] ? k + 1 : k); merged = true; break; }
            }
        }
        List<int[]> out = new ArrayList<>();
        for (int k = 0; k < peaks.size(); k++) {
            int x = peaks.get(k);
            int lim0 = k == 0 ? a : argMin(sm, peaks.get(k - 1), x), lim1 = k == peaks.size() - 1 ? b : argMin(sm, x, peaks.get(k + 1));
            int lo = x, hi = x;
            while (lo - 1 > lim0 && sm[lo - 1] >= sm[x] - drop) lo--;
            while (hi + 1 < lim1 && sm[hi + 1] >= sm[x] - drop) hi++;
            while (hi - lo < onePt) { if (lo > lim0 && (hi >= lim1 || sm[lo - 1] >= sm[Math.min(n - 1, hi + 1)])) lo--; else if (hi < lim1) hi++; else break; }
            out.add(new int[]{p.low + lo, p.low + hi});
        }
        return out;
    }

    private static int argMin(double[] v, int a, int b) { int m = a; for (int i = a; i <= b; i++) if (v[i] < v[m]) m = i; return m; }

    /** Полоса вокруг POC до обрыва: строка входит, пока её TPO ≥ share × максимум и не ниже
     *  максимума предыдущих look строк (в сторону POC) на cliff принтов и больше. */
    static int[] cliffBand(Profile p, int pocTicks, double share, int cliff, int look) {
        int[] c = p.counts; int i = pocTicks - p.low; double thr = share * p.max();
        int lo = i, hi = i;
        while (hi + 1 < c.length && c[hi + 1] >= thr && (cliff <= 0 || c[hi + 1] > maxOf(c, Math.max(i, hi - look + 1), hi) - cliff)) hi++;
        while (lo - 1 >= 0 && c[lo - 1] >= thr && (cliff <= 0 || c[lo - 1] > maxOf(c, lo, Math.min(i, lo + look - 1)) - cliff)) lo--;
        return new int[]{p.low + lo, p.low + hi};
    }

    private static int maxOf(int[] c, int a, int b) { int m = 0; for (int k = a; k <= b; k++) m = Math.max(m, c[k]); return m; }

    /** Полоса [lo, hi] шире maxW тиков → окно шириной maxW внутри неё, содержащее POC, с максимумом TPO
     *  (при равенстве — с POC ближе к середине). */
    static int[] narrow(Profile p, int[] band, int poc, int maxW) {
        if (maxW <= 0 || band[1] - band[0] <= maxW) return band;
        int bestLo = band[0]; long best = -1; double bestOff = Double.MAX_VALUE;
        for (int lo = Math.max(band[0], poc - maxW); lo <= Math.min(poc, band[1] - maxW); lo++) {
            long sum = 0;
            for (int t = lo; t <= lo + maxW; t++) sum += p.counts[t - p.low];
            double off = Math.abs(lo + maxW / 2.0 - poc);
            if (sum > best || (sum == best && off < bestOff)) { best = sum; bestLo = lo; bestOff = off; }
        }
        return new int[]{bestLo, bestLo + maxW};
    }

    // ------------------------------------------------------------------ первородные зоны
    private static double overlap(int lo1, int hi1, int lo2, int hi2) {
        int inter = Math.min(hi1, hi2) - Math.max(lo1, lo2) + 1;
        return Math.max(inter, 0) / (double) Math.min(hi1 - lo1 + 1, hi2 - lo2 + 1);
    }

    private static final class PZ {
        int lo, hi; final List<LocalDate> days = new ArrayList<>(); LocalDate fixedOn;
        final List<LocalDate> conf = new ArrayList<>();
    }

    private static List<Zone> primaryZones(List<Day> full, int minTpo, int maxTpo, Params p,
                                           Map<LocalDate, LocalDate> nextTradeDay, int kind) {
        double ovMin = p.mergeOverlapMin;
        int maxW = (int) Math.round(p.mergeMaxWidthPts / p.tick);
        List<PZ> fixed = new ArrayList<>();
        PZ cur = null;
        for (Day d : full) {
            boolean ok = d.hvnTpo >= minTpo && d.hvnTpo <= maxTpo;
            if (cur != null) {
                if (overlap(cur.lo, cur.hi, d.bandLo, d.bandHi) >= ovMin) {
                    if (ok && Math.max(cur.hi, d.bandHi) - Math.min(cur.lo, d.bandLo) <= maxW) {
                        cur.lo = Math.min(cur.lo, d.bandLo); cur.hi = Math.max(cur.hi, d.bandHi);
                        cur.days.add(d.date);
                    }
                    continue;
                }
                cur.fixedOn = d.date; fixed.add(cur); cur = null;
            }
            if (!ok) continue;
            PZ best = null; double bestOv = -1;
            for (PZ z : fixed) {
                double ov = overlap(z.lo, z.hi, d.bandLo, d.bandHi);
                if (ov >= ovMin && ov > bestOv) { best = z; bestOv = ov; }
            }
            if (best != null) best.conf.add(d.date);
            else { cur = new PZ(); cur.lo = d.bandLo; cur.hi = d.bandHi; cur.days.add(d.date); }
        }
        List<Zone> out = new ArrayList<>();
        for (PZ z : fixed) {
            LocalDate av = nextTradeDay.get(z.fixedOn);
            if (av == null) continue;
            Zone r = new Zone();
            r.k = kind; r.lo = z.lo * p.tick; r.hi = z.hi * p.tick; r.av = av;
            r.src = "hvn"; r.formationDays = z.days.size(); r.conf = new ArrayList<>(z.conf);
            out.add(r);
        }
        return out;
    }

    // ------------------------------------------------------------------ отторжения
    private static final int W_NONE = 0, W_INSIDE = 1, W_BOUNDARY = 2, W_OUTSIDE = 3;

    /** 0 — нет / проторговка, 1 — лонг-отторжение, 2 — шорт-отторжение (tpo/zone_classes.py). */
    static int interaction(double o, double h, double l, double c, double lo, double hi, double mw) {
        double bot = Math.min(o, c), top = Math.max(o, c);
        int lower = W_OUTSIDE, upper = W_OUTSIDE;
        if (l >= lo && bot <= hi) lower = W_INSIDE;
        if ((l <= hi && bot > hi) || (l <= lo && bot > lo)) lower = W_BOUNDARY;
        if (bot - l < mw) lower = W_NONE;
        if (top >= lo && h <= hi) upper = W_INSIDE;
        if ((top < lo && h >= lo) || (top < hi && h >= hi)) upper = W_BOUNDARY;
        if (h - top < mw) upper = W_NONE;
        boolean touch = l <= hi && h >= lo;
        int out = 0;
        if (touch && lower == W_BOUNDARY && c > hi) out = 1;
        if (touch && upper == W_BOUNDARY && c < lo) out = 2;
        return out;
    }

    // ------------------------------------------------------------------ подгонка границ
    static double[] alignZone(List<Bar> bars, double lo, double hi, double tick, double search, double minWick,
                              double wPlain, double minScore, double minWidth, double maxWidth) {
        int n = bars.size();
        double[] top = new double[n], bot = new double[n];
        boolean[] upW = new boolean[n], dnW = new boolean[n];
        for (int i = 0; i < n; i++) {
            Bar b = bars.get(i);
            top[i] = Math.max(b.o, b.c); bot[i] = Math.min(b.o, b.c);
            upW[i] = (b.h - top[i]) >= minWick; dnW[i] = (bot[i] - b.l) >= minWick;
        }
        double[] h = best(hi, top, bot, upW, dnW, tick, search, wPlain, minScore);
        double[] l = best(lo, top, bot, upW, dnW, tick, search, wPlain, minScore);
        double nh = h[0], nl = l[0];
        if (!(minWidth <= nh - nl && nh - nl <= maxWidth)) return new double[]{lo, hi};
        return new double[]{nl, nh};
    }

    private static double[] best(double edge, double[] top, double[] bot, boolean[] upW, boolean[] dnW,
                                 double tick, double search, double wPlain, double minScore) {
        double start = edge - search, stop = edge + search + tick / 2;
        int len = (int) Math.ceil((stop - start) / tick);
        if (len <= 0) return new double[]{edge, 0};
        double[] grid = new double[len], sc = new double[len], ex = new double[len];
        double topScore = Double.NEGATIVE_INFINITY;
        for (int g = 0; g < len; g++) {
            grid[g] = Math.rint((start + g * tick) / tick) * tick;
            sc[g] = score(grid[g], top, bot, upW, dnW, tick + 1e-9, wPlain);
            ex[g] = score(grid[g], top, bot, upW, dnW, 1e-9, wPlain);
            topScore = Math.max(topScore, sc[g]);
        }
        if (topScore < minScore) return new double[]{edge, topScore};
        double kmax = Double.NEGATIVE_INFINITY;
        for (int g = 0; g < len; g++) kmax = Math.max(kmax, sc[g] + 1e-3 * ex[g]);
        double bestV = edge, bestD = Double.MAX_VALUE;
        for (int g = 0; g < len; g++) {
            if (sc[g] + 1e-3 * ex[g] != kmax) continue;
            double d = Math.abs(grid[g] - edge);
            if (d < bestD) { bestD = d; bestV = grid[g]; }
        }
        return new double[]{bestV, topScore};
    }

    private static double score(double g, double[] top, double[] bot, boolean[] upW, boolean[] dnW,
                                double tol, double wPlain) {
        int anchor = 0, plain = 0;
        for (int i = 0; i < top.length; i++) {
            boolean nt = Math.abs(top[i] - g) <= tol, nb = Math.abs(bot[i] - g) <= tol;
            boolean an = (nt && upW[i]) || (nb && dnW[i]);
            if (an) anchor++; else if (nt || nb) plain++;
        }
        return anchor + wPlain * plain;
    }

    // ------------------------------------------------------------------ состояние и фильтры
    public static State stateAt(Zone z, long o, Params p, long[] fullOrd) {
        State st = new State();
        st.nl = lowerBound(z.L, o); st.ns = lowerBound(z.S, o);
        int tot = st.nl + st.ns;
        if (p.compositeMode && z.k == K_CORE) {
            st.kind = Kind.CORE; st.rej = tot; st.purity = tot >= 1 ? Math.max(st.nl, st.ns) / (double) tot : null;
            st.age = o - z.avOrd(); st.conf = lowerBound(z.coreConf, o);
            return st;
        }
        if (p.compositeMode && z.k == K_SESS) {
            int ri = lowerBound(z.rcAt, o + 1) - 1;                 // последняя синхронизация к сессии o
            boolean rc = ri >= 0;
            st.kind = rc ? z.rcKind[ri] : z.partKind; st.comp = rc ? (z.rcComp[ri].live ? z.rcComp[ri] : null) : z.partOf;
            st.recolored = rc;
            // минорная — серая; с minorEnd (сильный узел на её месте): sessMinorPromote — обычная зона с ролью, посчитанной при
            // рождении, иначе вытеснена — не рисуется
            if (z.minor && o < z.minorEnd) st.kind = Kind.GREY;
            else if (z.minor && !p.sessMinorPromote) st.kind = null;
            else if (z.narrow && !(p.gridNarrowKeepBlue && st.kind == Kind.CORE)) st.kind = Kind.GREY;   // уже квадрата — серая (кроме синей)
            st.rej = tot; st.purity = tot >= 1 ? Math.max(st.nl, st.ns) / (double) tot : null;
            st.age = o - z.avOrd(); st.conf = lowerBound(z.coreConf, o);
            return st;
        }
        if (p.compositeMode && z.k == K_PART) {
            st.kind = z.partKind; st.comp = z.partOf; st.rej = tot; st.purity = tot >= 1 ? Math.max(st.nl, st.ns) / (double) tot : null;
            st.age = o - z.avOrd(); st.conf = lowerBound(z.coreConf, o);
            return st;
        }
        if (p.compositeMode && p.partition && (z.k == K_HVN || z.k == K_GREY) && o >= z.hideFrom) { st.kind = null; return st; }
        if (p.compositeMode && z.fixedGrey) {
            st.kind = Kind.GREY; st.rej = tot; st.purity = tot >= 1 ? Math.max(st.nl, st.ns) / (double) tot : null;
            st.age = o - z.avOrd();
            return st;
        }
        if (p.compositeMode && (z.k == K_HVN || z.k == K_GREY)) {
            // класс фиксируется по композиту, правившему при появлении зоны (или по первому, накрывшему её позже)
            int gi = lowerBound(z.govAv, z.avOrd() + 1) - 1;
            if (gi < 0 && z.govAv.length > 0) gi = 0;
            if (gi >= 0 && z.govAv[gi] > o) gi = -1;          // этот композит ещё не сложился — пока серая
            // зона, родившаяся во время композита, переходит к нему, когда он сложится
            for (int k = z.gov.length - 1; k >= 0; k--) {
                Comp c = z.gov[k];
                if (!c.start.isAfter(z.av) && !z.av.isAfter(c.av)) { if (c.av.toEpochDay() <= o) gi = k; break; }
            }
            Kind kind;
            if (gi < 0) kind = Kind.GREY;
            else {
                Comp c = z.gov[gi]; st.comp = c;
                Zone core = c.core;
                double w = Math.max(z.bhi - z.blo, p.tick);
                double inter = Math.min(z.bhi, core.bhi) - Math.max(z.blo, core.blo);
                double mid = (z.blo + z.bhi) / 2;
                kind = inter > 0 && inter / w > p.coreAbsorb ? null : mid > core.bhi ? Kind.HVN_R : mid < core.blo ? Kind.HVN_S : null;
            }
            st.kind = kind; st.rej = tot; st.purity = tot >= 1 ? Math.max(st.nl, st.ns) / (double) tot : null;
            st.age = o - z.avOrd();
            int conf = 0; long cnear = Math.round(p.confirmNearPts * 100);
            for (long[] c : z.cf) { if (c[0] >= o) break; if (c[1] <= cnear) conf++; }
            st.conf = conf;
            return st;
        }
        Kind kind = z.k == K_OLD_BALANCE ? Kind.OLD_BALANCE : z.k == K_GREY ? Kind.GREY : null;
        if (kind == null && z.young && (z.greyUntil == null || o < z.greyUntil.toEpochDay())) kind = Kind.GREY;
        if (kind == Kind.GREY) {
            Long pr = promotedAt(z, p, fullOrd);
            if (pr != null && o >= pr) { kind = null; st.promoted = true; }
        }
        if (kind == null) {
            kind = tot == 0 ? Kind.HVN_NEW : (double) st.nl / tot >= p.rejectionShare ? Kind.HVN_S
                    : (double) st.ns / tot >= p.rejectionShare ? Kind.HVN_R : Kind.HVN_RANGE;
        }
        st.kind = kind;
        st.rej = tot;
        st.purity = tot >= 1 ? Math.max(st.nl, st.ns) / (double) tot : null;
        st.age = o - z.avOrd();
        int conf = 0;
        long cnear = Math.round(p.confirmNearPts * 100);
        for (long[] c : z.cf) { if (c[0] >= o) break; if (c[1] <= cnear) conf++; }
        st.conf = conf;
        return st;
    }

    /** День перевода серой зоны в основную или null (≥ promoteRejections отторжений за сессии подгонки). */
    static Long promotedAt(Zone z, Params p, long[] fullOrd) {
        if (p.alignSessions <= 0 || p.promoteRejections <= 0) return null;
        int idx = z.ai + p.alignSessions;
        if (idx >= fullOrd.length) return null;
        long f = fullOrd[idx];
        return lowerBound(z.L, f) + lowerBound(z.S, f) >= p.promoteRejections ? f : null;
    }

    static boolean passes(Zone z, State st, Params p) {
        if (st.kind == null || !p.types.contains(st.kind)) return false;
        if (st.conf < p.minConfirmations || st.rej < p.minRejections || st.age < p.minAgeDays) return false;
        if (p.purityMinPct > 0 && (st.purity == null || st.purity * 100 < p.purityMinPct)) return false;
        if (p.purityMaxPct < 100 && (st.purity == null || st.purity * 100 > p.purityMaxPct)) return false;
        return !(p.maxWidthPts < 60 && z.bhi - z.blo > p.maxWidthPts);
    }

    // ------------------------------------------------------------------ утилиты
    static int ticks(double price, double tick) { return (int) Math.rint(price / tick); }

    static int lowerBound(long[] a, long v) {
        int lo = 0, hi = a.length;
        while (lo < hi) { int m = (lo + hi) >>> 1; if (a[m] < v) lo = m + 1; else hi = m; }
        return lo;
    }

    // ------------------------------------------------------------------ последние сессии
    /** Тесты и отторжения зоны на 30m-барах (все бары — ETH + RTH) последних сессий. */
    public static final class Recent {
        public final Zone z; public int tests, longs, shorts;
        Recent(Zone z) { this.z = z; }
        public int rejections() { return longs + shorts; }
    }

    /** Для каждой зоны, видимой хотя бы в одну из последних {@code lastSessions} показываемых сессий:
     *  тест — бар задел зону (границы после подгонки); отторжение — хвост пересёк границу и close
     *  за зоной (правила tpo/zone_classes.py). Сортировка: отторжения, затем тесты, по убыванию. */
    public static List<Recent> recent(List<Bar> bars, Result res, Params p, int lastSessions) {
        int n = res.sessions.size(), from = Math.max(0, n - lastSessions);
        if (n == 0) return List.of();
        long t0 = res.sessions.get(from).startMs;
        Map<Zone, Recent> out = new java.util.LinkedHashMap<>();
        for (int i = from; i < n; i++) for (Shown v : res.visible.get(i)) out.computeIfAbsent(v.z, Recent::new);
        double mw = p.minWickTicks * p.tick;
        if (!res.bars.isEmpty()) bars = res.bars;              // цены после склейки роллов
        for (Bar b : bars) {
            if (b.t < t0) continue;
            LocalDateTime lt = LocalDateTime.ofInstant(Instant.ofEpochMilli(b.t), p.tz);
            LocalDate d = lt.getHour() * 60 + lt.getMinute() >= p.ethStartMin ? lt.toLocalDate().plusDays(1) : lt.toLocalDate();
            long ord = d.toEpochDay();
            for (Recent r : out.values()) {
                if (r.z.avOrd() > ord) continue;              // зона ещё не появилась
                if (b.l > r.z.bhi || b.h < r.z.blo) continue;
                r.tests++;
                int t = interaction(b.o, b.h, b.l, b.c, r.z.blo, r.z.bhi, mw);
                if (t == 1) r.longs++; else if (t == 2) r.shorts++;
            }
        }
        List<Recent> l = new ArrayList<>(out.values());
        l.sort((a, b) -> a.rejections() != b.rejections() ? b.rejections() - a.rejections() : b.tests - a.tests);
        return l;
    }

    private ZoneEngine() {}
}
