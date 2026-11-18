package patterns.zones;
import java.awt.*; import java.awt.image.BufferedImage; import java.io.*; import java.time.*; import java.util.*; import java.util.List;
import javax.imageio.ImageIO;
import patterns.zones.ZoneEngine.*;
/** Композиты, как их строит движок, поверх 30m-баров: java CompView bars.csv out.png from to [дата данных] */
public class CompView {
  public static void main(String[] a) throws Exception {
    List<Bar> bars = Check.read(a[0]);                // -Dfrom / -Dto — как в Check
    ZoneId tz = ZoneId.of("America/Chicago");
    LocalDate from = LocalDate.parse(a[2]), to = LocalDate.parse(a[3]);
    long end = to.plusDays(1).atTime(16, 0).atZone(tz).toInstant().toEpochMilli();
    bars.removeIf(b -> b.t >= end);
    Params p = new Params(); p.compositeMode = true; p.zoneMaxWidthPts = 10;
    Result res = ZoneEngine.run(bars, p);
    List<Bar> bs = res.bars; long t0 = from.atStartOfDay(tz).toInstant().toEpochMilli();
    int i0 = 0; while (i0 < bs.size() && bs.get(i0).t < t0) i0++;
    int n = bs.size() - i0;
    double lo = 1e9, hi = -1e9; for (int i = i0; i < bs.size(); i++) { lo = Math.min(lo, bs.get(i).l); hi = Math.max(hi, bs.get(i).h); }
    lo -= 10; hi += 10;
    int W = 2200, H = 1150, L = 10, R = 90, T = 40, B = 40;
    BufferedImage img = new BufferedImage(W, H, BufferedImage.TYPE_INT_RGB);
    Graphics2D g = img.createGraphics(); g.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
    g.setColor(new Color(0x111316)); g.fillRect(0, 0, W, H);
    final double fl = lo, fh = hi; final int fi0 = i0;
    java.util.function.DoubleUnaryOperator ty = v -> T + (fh - v) / (fh - fl) * (H - T - B);
    java.util.function.LongToDoubleFunction tx = t -> { int k = Collections.binarySearch(bs.stream().map(b -> b.t).toList(), t); if (k < 0) k = -k - 1; return L + (k - fi0) * (double) (W - L - R) / n; };
    g.setFont(new Font("SansSerif", Font.PLAIN, 13));
    for (double y = Math.ceil(lo / 10) * 10; y <= hi; y += 10) { int yy = (int) ty.applyAsDouble(y); g.setColor(new Color(0x1e2228)); g.drawLine(L, yy, W - R, yy); g.setColor(new Color(0x8a9097)); if (((int) y) % 20 == 0) g.drawString(String.valueOf((int) y), W - R + 6, yy + 4); }
    LocalDate prev = null;
    for (int i = i0; i < bs.size(); i++) {
      Bar b = bs.get(i); LocalDate d = Instant.ofEpochMilli(b.t).atZone(tz).toLocalDate();
      double x = L + (i - i0) * (double) (W - L - R) / n;
      if (!d.equals(prev) && d.getDayOfWeek() == DayOfWeek.MONDAY) { g.setColor(new Color(0x262b31)); g.drawLine((int) x, T, (int) x, H - B); g.setColor(new Color(0x8a9097)); g.drawString(d.getDayOfMonth() + "." + String.format("%02d", d.getMonthValue()), (int) x + 2, H - 15); }
      prev = d;
      g.setColor(new Color(0x9aa0a6)); g.drawLine((int) x, (int) ty.applyAsDouble(b.h), (int) x, (int) ty.applyAsDouble(b.l));
    }
    Object[][] kinds = {{res.comps, new Color(0x3d7bff), "баланс"}, {res.subComps, new Color(0xffa040), "уточнение"}, {res.notBalance, new Color(0x999999), "не баланс"}};
    int lab = 0;
    for (Object[] k : kinds) {
      @SuppressWarnings("unchecked") List<Comp> cs = (List<Comp>) k[0]; Color c = (Color) k[1];
      for (Comp cp : cs) {
        if (cp.end.isBefore(from)) continue;
        double x0 = tx.applyAsDouble(cp.start.atTime(8, 30).atZone(tz).toInstant().toEpochMilli()), x1 = tx.applyAsDouble(cp.end.atTime(15, 0).atZone(tz).toInstant().toEpochMilli());
        double y0 = ty.applyAsDouble(cp.hi), y1 = ty.applyAsDouble(cp.lo);
        g.setColor(c); g.setStroke(k[2].equals("баланс") ? new BasicStroke(2f) : new BasicStroke(2f, BasicStroke.CAP_BUTT, BasicStroke.JOIN_MITER, 10f, new float[]{8f, 5f}, 0f));
        g.draw(new java.awt.geom.Rectangle2D.Double(x0, y0, x1 - x0, y1 - y0));
        if (cp.core != null && cp.core.hi > 0) { g.setColor(new Color(c.getRed(), c.getGreen(), c.getBlue(), 110)); double cy0 = ty.applyAsDouble(cp.core.hi), cy1 = ty.applyAsDouble(cp.core.lo); g.fill(new java.awt.geom.Rectangle2D.Double(x0, cy0, x1 - x0, Math.max(2, cy1 - cy0))); }
        String s = String.format(Locale.ROOT, "%s %s–%s (%d дн.) %.2f–%.2f%s%s", k[2], cp.start.getDayOfMonth() + "." + String.format("%02d", cp.start.getMonthValue()), cp.end.getDayOfMonth() + "." + String.format("%02d", cp.end.getMonthValue()), cp.days, cp.lo, cp.hi,
          cp.core != null && cp.core.hi > 0 ? String.format(Locale.ROOT, ", ядро %.2f–%.2f", cp.core.lo, cp.core.hi) : "", k[2].equals("баланс") ? (cp.primary ? ", ПЕРВИЧНЫЙ" : ", вторичный") : "");
        g.setColor(c); g.drawString(s, (int) x0 + 4, (int) y0 - 6 - 15 * (lab++ % 2));
      }
    }
    g.setFont(new Font("SansSerif", Font.BOLD, 15)); g.setColor(Color.WHITE);
    g.drawString("Композиты по алгоритму (ESZ6, цены графика). Сплошная рамка — баланс, оранжевый пунктир — уточнение, серый — не баланс; заливка — ядро", 12, 22);
    ImageIO.write(img, "png", new File(a[1]));
  }
}
