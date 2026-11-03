#version 440
// Reveals a pre-drawn arc (and its lit ticks) from angle a0 round to a0 + sweep * u. `u` is animated on the
// render thread by a UniformAnimator; nothing is redrawn on the GUI thread while it moves.
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float u;
    float a0;
    float sweep;
    vec2 center;
};
layout(binding = 1) uniform sampler2D src;
void main() {
    vec2 p = qt_TexCoord0 - center;
    float rel = mod(atan(p.y, p.x) - a0 + 6.28318530718, 6.28318530718);
    float lim = sweep * u;
    float m = 1.0 - smoothstep(lim - 0.012, lim, rel);
    fragColor = texture(src, qt_TexCoord0) * (m * qt_Opacity);
}
