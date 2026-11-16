package com.motivewave.platform.sdk.common;
public interface DataContext {
    BarSize getChartBarSize();
    DataSeries getDataSeries();
    DataSeries getDataSeries(BarSize size);
    Instrument getInstrument();
}
