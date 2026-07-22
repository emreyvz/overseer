// OVERSEER — WebGL2 Live-View renderer: barrel/pincushion lens + chromatic aberration
// + vignette + scanlines + subtle bloom (brief §1 "Merkezi Live View'e hafif pincushion").
// Falls back to null when WebGL2 is unavailable (PovView then shows the plain feed).

export interface FeedGL {
  setSource(el: HTMLImageElement | HTMLVideoElement | null): void
  destroy(): void
}

const VERT = `#version 300 es
in vec2 p; out vec2 uv;
void main(){ uv = p * 0.5 + 0.5; gl_Position = vec4(p, 0.0, 1.0); }`

const FRAG = `#version 300 es
precision highp float;
in vec2 uv; out vec4 o;
uniform sampler2D tex;
uniform vec2 res;
uniform float t;
uniform float hasSrc;

float hash(vec2 v){ return fract(sin(dot(v, vec2(12.9898,78.233))) * 43758.5453); }

vec3 sampleSrc(vec2 c){
  if(hasSrc > 0.5){
    // desaturated surveillance look
    vec3 s = texture(tex, c).rgb;
    float g = dot(s, vec3(0.299,0.587,0.114));
    return mix(vec3(g), s, 0.55) * 0.92;
  }
  // procedural dark backdrop so the lens is visible with no feed
  float grid = step(0.985, fract(c.x*40.0)) + step(0.985, fract(c.y*24.0));
  float glow = 0.05 + 0.04*exp(-8.0*length(c-0.5));
  return vec3(glow) + grid*0.02;
}

void main(){
  vec2 c = uv - 0.5;
  float r2 = dot(c, c);
  // pincushion/barrel distortion (subtle)
  vec2 d = c * (1.0 + 0.14 * r2);
  vec2 st = d + 0.5;

  // chromatic aberration grows toward edges
  float ca = 0.0016 + 0.006 * r2;
  vec2 dir = normalize(c + 1e-5);
  vec3 col;
  col.r = sampleSrc(st + dir*ca).r;
  col.g = sampleSrc(st).g;
  col.b = sampleSrc(st - dir*ca).b;

  // letterbox where distortion pushes outside the frame
  if(st.x < 0.0 || st.x > 1.0 || st.y < 0.0 || st.y > 1.0) col *= 0.0;

  // subtle bloom on brights
  float lum = dot(col, vec3(0.299,0.587,0.114));
  col += smoothstep(0.7, 1.0, lum) * 0.12;

  // scanlines + slow roll
  col *= 0.86 + 0.14 * sin((uv.y*res.y*1.4) + t*2.0);
  // vignette
  col *= smoothstep(0.9, 0.35, r2);
  // grain
  col += (hash(uv*res + t) - 0.5) * 0.03;

  o = vec4(col, 1.0);
}`

export function initFeedGL(canvas: HTMLCanvasElement): FeedGL | null {
  const gl = canvas.getContext('webgl2', { antialias: false, premultipliedAlpha: false })
  if (!gl) return null

  const compile = (type: number, src: string) => {
    const s = gl.createShader(type)!
    gl.shaderSource(s, src); gl.compileShader(s)
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) { console.warn(gl.getShaderInfoLog(s)); return null }
    return s
  }
  const vs = compile(gl.VERTEX_SHADER, VERT)
  const fs = compile(gl.FRAGMENT_SHADER, FRAG)
  if (!vs || !fs) return null
  const prog = gl.createProgram()!
  gl.attachShader(prog, vs); gl.attachShader(prog, fs); gl.linkProgram(prog)
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) { console.warn(gl.getProgramInfoLog(prog)); return null }
  gl.useProgram(prog)
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true)  // images are top-left origin; flip so video isn't upside-down

  const buf = gl.createBuffer()
  gl.bindBuffer(gl.ARRAY_BUFFER, buf)
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)
  const loc = gl.getAttribLocation(prog, 'p')
  gl.enableVertexAttribArray(loc)
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0)

  const tex = gl.createTexture()
  gl.bindTexture(gl.TEXTURE_2D, tex)
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([10, 12, 15, 255]))
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)

  const uRes = gl.getUniformLocation(prog, 'res')
  const uT = gl.getUniformLocation(prog, 't')
  const uHas = gl.getUniformLocation(prog, 'hasSrc')

  let source: HTMLImageElement | HTMLVideoElement | null = null
  let raf = 0
  const start = performance.now()

  const ready = (el: HTMLImageElement | HTMLVideoElement) =>
    (el instanceof HTMLVideoElement ? el.readyState >= 2 : el.complete && el.naturalWidth > 0)

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    const w = Math.max(2, Math.floor(canvas.clientWidth * dpr))
    const h = Math.max(2, Math.floor(canvas.clientHeight * dpr))
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h }
  }

  function frame() {
    resize()
    gl!.viewport(0, 0, canvas.width, canvas.height)
    let has = 0
    if (source && ready(source)) {
      try {
        gl!.bindTexture(gl!.TEXTURE_2D, tex)
        gl!.texImage2D(gl!.TEXTURE_2D, 0, gl!.RGBA, gl!.RGBA, gl!.UNSIGNED_BYTE, source)
        has = 1
      } catch { has = 0 }
    }
    gl!.uniform2f(uRes, canvas.width, canvas.height)
    gl!.uniform1f(uT, (performance.now() - start) / 1000)
    gl!.uniform1f(uHas, has)
    gl!.drawArrays(gl!.TRIANGLES, 0, 3)
    raf = requestAnimationFrame(frame)
  }
  raf = requestAnimationFrame(frame)

  return {
    setSource(el) { source = el },
    destroy() { cancelAnimationFrame(raf); gl.deleteProgram(prog); gl.deleteTexture(tex); gl.deleteBuffer(buf) },
  }
}
