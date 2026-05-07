// Minimal WebGL fullscreen-fragment-shader runner.
// Usage: new ShaderRunner({ canvas, fragmentSource, uniforms });
(function () {
  const VERT = `
    attribute vec2 a_pos;
    void main() { gl_Position = vec4(a_pos, 0.0, 1.0); }
  `;

  function compile(gl, type, src) {
    const sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      const log = gl.getShaderInfoLog(sh);
      console.error('Shader compile error:', log, '\n', src);
      throw new Error(log);
    }
    return sh;
  }

  function program(gl, fragSrc) {
    const vs = compile(gl, gl.VERTEX_SHADER, VERT);
    const fs = compile(gl, gl.FRAGMENT_SHADER, fragSrc);
    const p = gl.createProgram();
    gl.attachShader(p, vs);
    gl.attachShader(p, fs);
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
      throw new Error(gl.getProgramInfoLog(p));
    }
    return p;
  }

  class ShaderRunner {
    constructor({ canvas, fragmentSource, onClick, pixelScale }) {
      this.canvas = canvas;
      // pixelScale < 1 downsamples the internal framebuffer for a chunky
      // pixel-art look when the canvas is upscaled via CSS image-rendering.
      this.pixelScale = (typeof pixelScale === 'number' && pixelScale > 0) ? pixelScale : null;
      const gl = canvas.getContext('webgl', { antialias: true, premultipliedAlpha: false });
      if (!gl) throw new Error('WebGL not supported');
      this.gl = gl;

      this.prog = program(gl, fragmentSource);
      gl.useProgram(this.prog);

      const buf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(
        gl.ARRAY_BUFFER,
        new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
        gl.STATIC_DRAW
      );
      const aPos = gl.getAttribLocation(this.prog, 'a_pos');
      gl.enableVertexAttribArray(aPos);
      gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

      this.u = {
        time: gl.getUniformLocation(this.prog, 'u_time'),
        res: gl.getUniformLocation(this.prog, 'u_res'),
        mouse: gl.getUniformLocation(this.prog, 'u_mouse'),
        mouseSmooth: gl.getUniformLocation(this.prog, 'u_mouseSmooth'),
        click: gl.getUniformLocation(this.prog, 'u_click'),
        clickTime: gl.getUniformLocation(this.prog, 'u_clickTime'),
        pressure: gl.getUniformLocation(this.prog, 'u_pressure'),
      };

      this.mouse = [0.5, 0.5];
      this.mouseSmooth = [0.5, 0.5];
      this.click = [0.5, 0.5];
      this.clickTime = -10.0;
      this.pressure = 0.0;
      this.targetPressure = 0.0;
      this.start = performance.now();

      this._resize = this._resize.bind(this);
      window.addEventListener('resize', this._resize);
      this._resize();

      const onMove = (x, y) => {
        const r = canvas.getBoundingClientRect();
        this.mouse[0] = (x - r.left) / r.width;
        this.mouse[1] = 1.0 - (y - r.top) / r.height;
      };
      window.addEventListener('mousemove', (e) => onMove(e.clientX, e.clientY), { passive: true });
      window.addEventListener('touchmove', (e) => {
        if (e.touches[0]) onMove(e.touches[0].clientX, e.touches[0].clientY);
      }, { passive: true });

      const doClick = (x, y) => {
        const r = canvas.getBoundingClientRect();
        this.click[0] = (x - r.left) / r.width;
        this.click[1] = 1.0 - (y - r.top) / r.height;
        this.clickTime = (performance.now() - this.start) / 1000;
        if (onClick) onClick(this.click);
      };
      window.addEventListener('mousedown', (e) => { doClick(e.clientX, e.clientY); this.targetPressure = 1.0; });
      window.addEventListener('mouseup', () => { this.targetPressure = 0.0; });
      window.addEventListener('touchstart', (e) => {
        if (e.touches[0]) { doClick(e.touches[0].clientX, e.touches[0].clientY); this.targetPressure = 1.0; }
      }, { passive: true });
      window.addEventListener('touchend', () => { this.targetPressure = 0.0; });

      this._loop = this._loop.bind(this);
      requestAnimationFrame(this._loop);
    }

    _resize() {
      const scale = this.pixelScale != null
        ? this.pixelScale
        : Math.min(window.devicePixelRatio || 1, 2);
      const w = this.canvas.clientWidth;
      const h = this.canvas.clientHeight;
      this.canvas.width = Math.max(1, Math.floor(w * scale));
      this.canvas.height = Math.max(1, Math.floor(h * scale));
      this.gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    }

    _loop() {
      const gl = this.gl;
      const t = (performance.now() - this.start) / 1000;

      // Smooth mouse
      this.mouseSmooth[0] += (this.mouse[0] - this.mouseSmooth[0]) * 0.08;
      this.mouseSmooth[1] += (this.mouseSmooth[1] - this.mouseSmooth[1]) * 0;
      this.mouseSmooth[1] += (this.mouse[1] - this.mouseSmooth[1]) * 0.08;
      this.pressure += (this.targetPressure - this.pressure) * 0.12;

      gl.useProgram(this.prog);
      if (this.u.time) gl.uniform1f(this.u.time, t);
      if (this.u.res) gl.uniform2f(this.u.res, this.canvas.width, this.canvas.height);
      if (this.u.mouse) gl.uniform2f(this.u.mouse, this.mouse[0], this.mouse[1]);
      if (this.u.mouseSmooth) gl.uniform2f(this.u.mouseSmooth, this.mouseSmooth[0], this.mouseSmooth[1]);
      if (this.u.click) gl.uniform2f(this.u.click, this.click[0], this.click[1]);
      if (this.u.clickTime) gl.uniform1f(this.u.clickTime, this.clickTime);
      if (this.u.pressure) gl.uniform1f(this.u.pressure, this.pressure);

      gl.drawArrays(gl.TRIANGLES, 0, 6);
      requestAnimationFrame(this._loop);
    }
  }

  window.ShaderRunner = ShaderRunner;
})();
