package com.motivewave.platform.sdk.study;
import java.lang.annotation.*;
@Retention(RetentionPolicy.RUNTIME) @Target(ElementType.TYPE)
public @interface StudyHeader {
    String namespace(); String id(); String name(); String label() default ""; String desc() default ""; String menu() default "";
    boolean overlay() default false; boolean studyOverlay() default false; boolean requiresVolume() default false;
    boolean supportsBarUpdates() default false;
}
