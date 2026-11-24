// Manual Zones (MW) — рисунки графика MotiveWave (зоны, боксы, горизонтали, карандаш) на графике ATAS X.
// Данные — ManualZones.Data.cs, генерируется scripts/mw_to_atas.py из data/manual_zones_ESZ6.mwml.
// Цвета — те же ARGB, что в MotiveWave; время точек — UTC (как IndicatorCandle.Time); цены — без сдвига.
namespace Patterns.Atas;

using System;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Drawing;

using ATAS.Indicators;

using OFT.Rendering.Context;
using OFT.Rendering.Tools;

[DisplayName("Manual Zones (MW)")]
[Category("Patterns")]
public partial class ManualZones : Indicator
{
	[Display(Name = "Zones", GroupName = "Show", Order = 10)]
	public bool ShowZones { get; set; } = true;

	[Display(Name = "Boxes", GroupName = "Show", Order = 20)]
	public bool ShowBoxes { get; set; } = true;

	[Display(Name = "Horizontal lines", GroupName = "Show", Order = 30)]
	public bool ShowLines { get; set; } = true;

	[Display(Name = "Pen drawings", GroupName = "Show", Order = 40)]
	public bool ShowPen { get; set; } = true;

	[Display(Name = "Hidden in MotiveWave", GroupName = "Show", Order = 50)]
	public bool ShowHidden { get; set; } = false;

	[Display(Name = "Line color (MW theme default)", GroupName = "Style", Order = 10)]
	public Color DefaultLineColor { get; set; } = Color.FromArgb(255, 192, 192, 192);

	[Display(Name = "Line width", GroupName = "Style", Order = 20)]
	[Range(1, 10)]
	public int LineWidth { get; set; } = 1;

	private DateTime[] _times = Array.Empty<DateTime>();

	public ManualZones()
		: base(true)
	{
		DenyToChangePanel = true;
		EnableCustomDrawing = true;
		DrawAbovePrice = false;
		SubscribeToDrawingEvents(DrawingLayouts.Historical);
		DataSeries[0].IsHidden = true;
	}

	protected override void OnCalculate(int bar, decimal value)
	{
		if (bar == 0 || _times.Length < bar + 1)
		{
			var t = new DateTime[Math.Max(bar + 1, _times.Length * 2)];
			Array.Copy(_times, t, Math.Min(_times.Length, bar));
			_times = t;
		}

		_times[bar] = GetCandle(bar).Time;
	}

	// x бара, в который попадает время t (UTC); за правым краем истории — экстраполяция по ширине бара
	private bool TryX(DateTime t, out int x)
	{
		x = 0;
		var n = CurrentBar;
		if (n < 2 || n > _times.Length)
			return false;

		if (t < _times[0])
			return false;

		if (t >= _times[n - 1])
		{
			var step = (_times[n - 1] - _times[n - 2]).Ticks;
			var w = ChartInfo.GetXByBar(n - 1) - ChartInfo.GetXByBar(n - 2);
			var k = step > 0 ? (t - _times[n - 1]).Ticks / step : 0;
			x = ChartInfo.GetXByBar(n - 1) + (int)(k * w);
			return true;
		}

		int lo = 0, hi = n - 1;            // последний бар с Time <= t
		while (lo < hi)
		{
			var mid = (lo + hi + 1) / 2;
			if (_times[mid] <= t)
				lo = mid;
			else
				hi = mid - 1;
		}

		x = ChartInfo.GetXByBar(lo);
		return true;
	}

	protected override void OnRender(RenderContext context, DrawingLayouts layout)
	{
		if (ChartInfo is null || Container is null || CurrentBar < 2)
			return;

		var region = Container.Region;

		foreach (var f in Figures)
		{
			if (f.Hidden && !ShowHidden)
				continue;

			switch (f.Kind)
			{
				case Zone when !ShowZones:
				case Box when !ShowBoxes:
				case HLine when !ShowLines:
					continue;
			}

			var t1 = new DateTime(f.T1, DateTimeKind.Utc);
			var t2 = new DateTime(f.T2, DateTimeKind.Utc);
			int x1, x2;

			if (f.ExtendLeft)
				x1 = region.Left;
			else if (!TryX(t1, out x1))
			{
				if (!f.ExtendRight && (!TryX(t2, out _)))
					continue;
				x1 = region.Left;          // начало раньше загруженной истории
			}

			if (f.ExtendRight)
				x2 = region.Right;
			else if (!TryX(t2, out x2))
				continue;

			if (x2 < region.Left || x1 > region.Right)
				continue;

			x1 = Math.Max(x1, region.Left - 1);
			x2 = Math.Min(x2, region.Right + 1);

			var yTop = ChartInfo.GetYByPrice(f.High, false);
			var yBot = ChartInfo.GetYByPrice(f.Low, false);

			if (f.Kind == HLine)
			{
				var c = f.Line == 0 ? DefaultLineColor : Color.FromArgb(unchecked((int)f.Line));
				context.DrawLine(new RenderPen(c, LineWidth), x1, yTop, x2, yTop);
				continue;
			}

			if (yBot < region.Top || yTop > region.Bottom)
				continue;

			var rect = new Rectangle(x1, yTop, Math.Max(1, x2 - x1), Math.Max(1, yBot - yTop));
			context.FillRectangle(Color.FromArgb(unchecked((int)f.Fill)), rect);

			if (f.Line != 0)
				context.DrawRectangle(new RenderPen(Color.FromArgb(unchecked((int)f.Line)), LineWidth), rect);
		}

		if (!ShowPen)
			return;

		var pen = new RenderPen(DefaultLineColor, LineWidth);

		foreach (var path in PenPaths)
		{
			int px = 0, py = 0;
			var have = false;

			for (var i = 0; i < path.Length; i += 2)
			{
				var t = new DateTime((long)path[i], DateTimeKind.Utc);
				if (!TryX(t, out var x))
				{
					have = false;
					continue;
				}

				var y = ChartInfo.GetYByPrice(path[i + 1], false);
				if (have)
					context.DrawLine(pen, px, py, x, y);

				px = x;
				py = y;
				have = true;
			}
		}
	}

	private const byte Zone = 0, Box = 1, HLine = 2;

	private readonly record struct Fig(byte Kind, long T1, long T2, decimal Low, decimal High,
		uint Fill, uint Line, bool ExtendRight, bool ExtendLeft, bool Hidden);
}
