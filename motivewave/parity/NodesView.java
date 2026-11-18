package patterns.zones;
import java.awt.*; import java.awt.image.BufferedImage; import java.io.*; import java.time.*; import java.util.*; import java.util.List;
import javax.imageio.ImageIO;
import patterns.zones.ZoneEngine.*;
/** Профиль композита и его узлы (как в прототипе синхронизации) + сессионные зоны в нём: истинные / минорные. */
public class NodesView {
  public static void main(String[] a) throws Exception {
    List<Bar> bars = Check.read(a[0]);                // -Dfrom / -Dto — как в Check
    long end = LocalDate.parse(a[2]).plusDays(1).atTime(16, 0).atZone(ZoneId.of("America/Chicago")).toInstant().toEpochMilli();
    bars.removeIf(b -> b.t >= end);
    
    Params p = new Params(); p.compositeMode = true; p.zoneMaxWidthPts = 10;
    Result res = ZoneEngine.run(bars, p);
    Comp c = null; for (Comp x : res.comps) if (x.start.toString().equals(a[3])) c = x;
    double tick = p.tick; Profile pf = c.prof; int[] cnt = pf.counts; int n = cnt.length, onePt = 4, half = 2;
    double[] sm = new double[n];
    for (int i = 0; i < n; i++) { double sum = 0; int k = 0; for (int j = Math.max(0, i - half); j <= Math.min(n - 1, i + half); j++) { sum += cnt[j]; k++; } sm[i] = sum / k; }
    double mx = 0; for (double v : sm) mx = Math.max(mx, v);
    int pi = c.pocT - pf.low, ba = pi, bb = pi;
    while (ba > 0 && sm[ba - 1] >= p.bodyShare * mx) ba--;
    while (bb < n - 1 && sm[bb + 1] >= p.bodyShare * mx) bb++;
    double PLO = pf.low * tick - 3, PHI = pf.high() * tick + 3;
    int W = 1600, H = 1200, T = 70, B = 30, X0 = 120, SC = Integer.getInteger("sc", 26);
    BufferedImage img = new BufferedImage(W, H, BufferedImage.TYPE_INT_RGB);
    Graphics2D g = img.createGraphics(); g.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
    g.setColor(new Color(0x111316)); g.fillRect(0, 0, W, H);
    java.util.function.DoubleUnaryOperator ty = v -> T + (PHI - v) / (PHI - PLO) * (H - T - B);
    g.setFont(new Font("SansSerif", Font.PLAIN, 13));
    for (double y = Math.ceil(PLO); y <= PHI; y += 1) { int yy = (int) ty.applyAsDouble(y); g.setColor(((int) y) % 5 == 0 ? new Color(0x2a2f36) : new Color(0x181b20)); g.drawLine(X0 - 10, yy, W - 20, yy); if (((int) y) % 5 == 0) { g.setColor(new Color(0x9aa0a6)); g.drawString(String.valueOf((int) y), 20, yy + 4); } }
    // тело
    g.setColor(new Color(255, 255, 255, 18)); g.fillRect(X0, (int) ty.applyAsDouble((pf.low + bb) * tick + tick), 900, (int) (ty.applyAsDouble((pf.low + ba) * tick) - ty.applyAsDouble((pf.low + bb) * tick + tick)));
    // узлы
    Color[] nc = {new Color(0x2e7dff), new Color(0xff9f1c), new Color(0x2ec4b6), new Color(0xe71d36), new Color(0xb388ff), new Color(0xffd166), new Color(0x06d6a0)};
    List<int[]> nds = ZoneEngine.compNodes(c, p);
    for (int k = 0; k < nds.size(); k++) {
      int[] q = nds.get(k); Color cc = nc[k % nc.length];
      int y0 = (int) ty.applyAsDouble(q[1] * tick + tick), y1 = (int) ty.applyAsDouble(q[0] * tick);
      g.setColor(new Color(cc.getRed(), cc.getGreen(), cc.getBlue(), 45)); g.fillRect(X0, y0, 1000, y1 - y0);
      g.setColor(cc); g.setStroke(new BasicStroke(2f)); g.drawRect(X0, y0, 1000, y1 - y0);
      g.drawString(String.format(Locale.ROOT, "узел %d: %.2f–%.2f, вершина %d TPO", k + 1, q[0] * tick, q[1] * tick + tick - tick, q[2]), X0 + 760, y0 + 16);
    }
    // профиль: TPO по тикам и сглаженный
    for (int i = 0; i < n; i++) {
      int yy = (int) ty.applyAsDouble((pf.low + i) * tick + tick), hh = Math.max(1, (int) (ty.applyAsDouble((pf.low + i) * tick) - ty.applyAsDouble((pf.low + i) * tick + tick)));
      g.setColor(i == pi ? new Color(0xe2662c) : new Color(0x7d858c)); g.fillRect(X0, yy, cnt[i] * SC, hh);
    }
    g.setColor(Color.WHITE); g.setStroke(new BasicStroke(1.5f));
    for (int i = 1; i < n; i++) g.drawLine(X0 + (int) (sm[i - 1] * SC), (int) ty.applyAsDouble((pf.low + i - 1) * tick + tick / 2), X0 + (int) (sm[i] * SC), (int) ty.applyAsDouble((pf.low + i) * tick + tick / 2));
    // VA композита
    { int[] va = ZoneEngine.valueArea(pf, p.valueAreaPct); g.setStroke(new BasicStroke(2f, BasicStroke.CAP_BUTT, BasicStroke.JOIN_MITER, 10f, new float[]{10f, 6f}, 0f)); g.setColor(new Color(0xffd166));
      for (double y : new double[]{va[1] * tick, va[2] * tick + tick}) { int yy = (int) ty.applyAsDouble(y); g.drawLine(X0 - 10, yy, W - 20, yy); }
      g.drawString(String.format(Locale.ROOT, "VAH %.2f", va[2] * tick + tick), X0 + 300, (int) ty.applyAsDouble(va[2] * tick + tick) - 6);
      g.drawString(String.format(Locale.ROOT, "VAL %.2f", va[1] * tick), X0 + 300, (int) ty.applyAsDouble(va[1] * tick) + 16); }
    // ядро
    g.setColor(new Color(0x3d6bff)); g.setStroke(new BasicStroke(3f)); int cy0 = (int) ty.applyAsDouble(c.core.hi), cy1 = (int) ty.applyAsDouble(c.core.lo);
    g.drawRect(X0 - 8, cy0, 4, cy1 - cy0); g.drawString("ядро", X0 - 50, (cy0 + cy1) / 2 + 4);
    // порог тела 15%
    g.setStroke(new BasicStroke(1f, BasicStroke.CAP_BUTT, BasicStroke.JOIN_MITER, 10f, new float[]{4f, 4f}, 0f)); g.setColor(new Color(0xaaaaaa));
    int xb = X0 + (int) (p.bodyShare * mx * SC); g.drawLine(xb, T, xb, H - B); g.drawString("15% максимума — граница тела", xb + 4, H - B - 6);
    // сессионные зоны композита: истинные / минорные (последняя сессия)
    int zx = X0 + 1030;
    g.setFont(new Font("SansSerif", Font.PLAIN, 12));
    for (Shown v : res.visible.get(res.visible.size() - 1)) {
      Zone z = v.z; if (z.k != ZoneEngine.K_SESS || z.hi < PLO || z.lo > PHI) continue;
      Comp cp = z.rcComp.length > 0 ? z.rcComp[z.rcComp.length - 1] : z.partOf;
      if (cp == null || !cp.start.equals(c.start)) continue;
      Color k = v.st.kind == Kind.CORE ? new Color(0x3d6bff) : v.st.kind == Kind.HVN_R ? new Color(0xc0392b) : v.st.kind == Kind.HVN_S ? new Color(0x1f9d3a) : new Color(0x8d959c);
      int y0 = (int) ty.applyAsDouble(z.hi), y1 = (int) ty.applyAsDouble(z.lo);
      g.setColor(k); g.fillRect(zx, y0, 60, Math.max(2, y1 - y0));
      g.setColor(new Color(0xdddddd)); g.drawString(String.format(Locale.ROOT, "%.0f–%.0f %s %s", z.lo, z.hi, z.born.getDayOfMonth() + "." + String.format("%02d", z.born.getMonthValue()), z.vaMajor == 1 ? "ГЛАВНАЯ ПОДДЕРЖКА у VAL" : z.vaMajor == 2 ? "ГЛАВНОЕ СОПРОТИВЛЕНИЕ у VAH" : z.syncMinor ? "минорная" : "ИСТИННАЯ (узел)"), zx + 66, (y0 + y1) / 2 + 4);
    }
    g.setFont(new Font("SansSerif", Font.BOLD, 15)); g.setColor(Color.WHITE);
    g.drawString(String.format(Locale.ROOT, "Профиль композита %s–%s (%d дн.): серые полосы — TPO по тикам (оранжевая — POC), белая линия — сглаженный 1 п.;", c.start, c.end, c.days), 12, 22);
    g.drawString("цветные рамки — узлы (провалы ≥ 15% меньшей вершины, узел — строки ≥ 75% вершины), жёлтый пунктир — VAL / VAH композита. Справа — зоны сессий.", 12, 42);
    ImageIO.write(img, "png", new File(a[1]));
  }
}
