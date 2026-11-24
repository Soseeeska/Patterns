namespace ATAS.Indicators
{
	using System;
	using System.Drawing;
	using OFT.Rendering.Context;
	[Flags] public enum DrawingLayouts { None = 0, Historical = 1, LatestBar = 2, Final = 4 }
	public class IndicatorCandle { public DateTime Time { get; set; } }
	public interface IChartInfo { int GetXByBar(int bar, bool isStartOfBar = true); int GetYByPrice(decimal price, bool isStartOfPrice = true); }
	public interface IChartContainer { Rectangle Region { get; } }
	public class ValueDataSeries { public bool IsHidden { get; set; } }
	public class DataSeriesList { public ValueDataSeries this[int i] { get => null; set { } } }
	public abstract class Indicator
	{
		protected Indicator(bool useCandles) { }
		public bool DenyToChangePanel { get; set; }
		public bool EnableCustomDrawing { get; set; }
		public bool DrawAbovePrice { get; set; }
		public DataSeriesList DataSeries { get; } = new();
		public IChartInfo ChartInfo => null;
		public IChartContainer Container => null;
		public int CurrentBar => 0;
		protected void SubscribeToDrawingEvents(DrawingLayouts l) { }
		public IndicatorCandle GetCandle(int bar) => null;
		protected abstract void OnCalculate(int bar, decimal value);
		protected virtual void OnRender(RenderContext context, DrawingLayouts layout) { }
	}
}
namespace OFT.Rendering.Tools { public class RenderPen { public RenderPen(System.Drawing.Color c, float width = 1) { } } }
namespace OFT.Rendering.Context
{
	using System.Drawing; using OFT.Rendering.Tools;
	public class RenderContext
	{
		public void FillRectangle(Color c, Rectangle r) { }
		public void DrawRectangle(RenderPen p, Rectangle r) { }
		public void DrawLine(RenderPen p, int x1, int y1, int x2, int y2) { }
	}
}
