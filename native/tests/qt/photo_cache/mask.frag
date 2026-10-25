#version 440
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 itemSize;
    float cornerRadius;
};
layout(binding = 1) uniform sampler2D source;
void main()
{
    vec2 point = (qt_TexCoord0 - vec2(0.5)) * itemSize;
    vec2 delta = abs(point) - (itemSize * 0.5 - vec2(cornerRadius));
    float distance = length(max(delta, vec2(0.0)))
        + min(max(delta.x, delta.y), 0.0) - cornerRadius;
    float mask = 1.0 - smoothstep(-1.0, 1.0, distance);
    fragColor = texture(source, qt_TexCoord0) * qt_Opacity * mask;
}
