#version 440
// The activity tape: ticks fade out towards the left edge and burn in the accent's bright core while they
// are fresh at the right edge. Runs on the render thread as the tape belt slides.
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float fadeLeft;
    float freshRight;
    vec4 hot;
};
layout(binding = 1) uniform sampler2D source;
void main() {
    vec4 c = texture(source, qt_TexCoord0);
    float f = smoothstep(0.0, fadeLeft, qt_TexCoord0.x);
    float fr = smoothstep(1.0 - freshRight, 1.0 - freshRight * 0.4, qt_TexCoord0.x);
    vec3 rgb = mix(c.rgb, hot.rgb * c.a, fr * 0.85);
    fragColor = vec4(rgb, c.a) * (f * qt_Opacity);
}
