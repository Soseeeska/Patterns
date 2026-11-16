package com.motivewave.platform.sdk.common;
public interface DataSeries {
    int size();
    long getStartTime(int i);
    float getOpen(int i);
    float getHigh(int i);
    float getLow(int i);
    float getClose(int i);
}
