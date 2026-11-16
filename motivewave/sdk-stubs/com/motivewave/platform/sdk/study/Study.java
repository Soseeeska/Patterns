package com.motivewave.platform.sdk.study;
import com.motivewave.platform.sdk.common.*;
import com.motivewave.platform.sdk.common.desc.SettingsDescriptor;
import com.motivewave.platform.sdk.draw.Figure;
public class Study {
    public void initialize(Defaults defaults) { }
    public Long getMinStartTime(DataContext ctx) { return null; }
    protected void calculateValues(DataContext ctx) { }
    protected void calculate(int index, DataContext ctx) { }
    public Settings getSettings() { throw new UnsupportedOperationException(); }
    public SettingsDescriptor createSD() { throw new UnsupportedOperationException(); }
    public RuntimeDescriptor createRD() { throw new UnsupportedOperationException(); }
    public void addFigures(java.util.Collection<? extends Figure> f) { }
    public void clearFigures() { }
    public void beginFigureUpdate() { }
    public void endFigureUpdate() { }
    public void info(String s) { }
    public void error(String s) { }
}
