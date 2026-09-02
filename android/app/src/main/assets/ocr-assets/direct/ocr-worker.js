"use strict";
(() => {
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __require = /* @__PURE__ */ ((x) => typeof require !== "undefined" ? require : typeof Proxy !== "undefined" ? new Proxy(x, {
    get: (a, b) => (typeof require !== "undefined" ? require : a)[b]
  }) : x)(function(x) {
    if (typeof require !== "undefined") return require.apply(this, arguments);
    throw Error('Dynamic require of "' + x + '" is not supported');
  });
  var __commonJS = (cb, mod) => function __require2() {
    return mod || (0, cb[__getOwnPropNames(cb)[0]])((mod = { exports: {} }).exports, mod), mod.exports;
  };

  // ../../tmp/ocr-runtime/node_modules/onnxruntime-web/dist/ort.wasm.min.js
  var require_ort_wasm_min = __commonJS({
    "../../tmp/ocr-runtime/node_modules/onnxruntime-web/dist/ort.wasm.min.js"(exports, module) {
      "use strict";
      var ort2 = (() => {
        var Je = Object.defineProperty;
        var On = Object.getOwnPropertyDescriptor;
        var Ln = Object.getOwnPropertyNames;
        var Pn = Object.prototype.hasOwnProperty;
        var qe = ((e) => typeof __require < "u" ? __require : typeof Proxy < "u" ? new Proxy(e, { get: (t, n) => (typeof __require < "u" ? __require : t)[n] }) : e)(function(e) {
          if (typeof __require < "u") return __require.apply(this, arguments);
          throw Error('Dynamic require of "' + e + '" is not supported');
        });
        var E = (e, t) => () => (e && (t = e(e = 0)), t);
        var Se = (e, t) => {
          for (var n in t) Je(e, n, { get: t[n], enumerable: true });
        }, _n = (e, t, n, o) => {
          if (t && typeof t == "object" || typeof t == "function") for (let r of Ln(t)) !Pn.call(e, r) && r !== n && Je(e, r, { get: () => t[r], enumerable: !(o = On(t, r)) || o.enumerable });
          return e;
        };
        var Ye = (e) => _n(Je({}, "__esModule", { value: true }), e);
        var Te, K, se, Dn, bt, Ze = E(() => {
          "use strict";
          Te = /* @__PURE__ */ new Map(), K = [], se = (e, t, n) => {
            if (t && typeof t.init == "function" && typeof t.createInferenceSessionHandler == "function") {
              let o = Te.get(e);
              if (o === void 0) Te.set(e, { backend: t, priority: n });
              else {
                if (o.priority > n) return;
                if (o.priority === n && o.backend !== t) throw new Error(`cannot register backend "${e}" using priority ${n}`);
              }
              if (n >= 0) {
                let r = K.indexOf(e);
                r !== -1 && K.splice(r, 1);
                for (let i = 0; i < K.length; i++) if (Te.get(K[i]).priority <= n) {
                  K.splice(i, 0, e);
                  return;
                }
                K.push(e);
              }
              return;
            }
            throw new TypeError("not a valid backend");
          }, Dn = async (e) => {
            let t = Te.get(e);
            if (!t) return "backend not found.";
            if (t.initialized) return t.backend;
            if (t.aborted) return t.error;
            {
              let n = !!t.initPromise;
              try {
                return n || (t.initPromise = t.backend.init(e)), await t.initPromise, t.initialized = true, t.backend;
              } catch (o) {
                return n || (t.error = `${o}`, t.aborted = true), t.error;
              } finally {
                delete t.initPromise;
              }
            }
          }, bt = async (e) => {
            let t = e.executionProviders || [], n = t.map((u) => typeof u == "string" ? u : u.name), o = n.length === 0 ? K : n, r, i = [], s = /* @__PURE__ */ new Set();
            for (let u of o) {
              let f = await Dn(u);
              typeof f == "string" ? i.push({ name: u, err: f }) : (r || (r = f), r === f && s.add(u));
            }
            if (!r) throw new Error(`no available backend found. ERR: ${i.map((u) => `[${u.name}] ${u.err}`).join(", ")}`);
            for (let { name: u, err: f } of i) n.includes(u) && console.warn(`removing requested execution provider "${u}" from session options because it is not available: ${f}`);
            let a = t.filter((u) => s.has(typeof u == "string" ? u : u.name));
            return [r, new Proxy(e, { get: (u, f) => f === "executionProviders" ? a : Reflect.get(u, f) })];
          };
        });
        var yt = E(() => {
          "use strict";
          Ze();
        });
        var gt, Et = E(() => {
          "use strict";
          gt = "1.27.0";
        });
        var St, _, Xe = E(() => {
          "use strict";
          Et();
          St = "warning", _ = { wasm: {}, webgl: {}, webgpu: {}, versions: { common: gt }, set logLevel(e) {
            if (e !== void 0) {
              if (typeof e != "string" || ["verbose", "info", "warning", "error", "fatal"].indexOf(e) === -1) throw new Error(`Unsupported logging level: ${e}`);
              St = e;
            }
          }, get logLevel() {
            return St;
          } };
          Object.defineProperty(_, "logLevel", { enumerable: true });
        });
        var A, Tt = E(() => {
          "use strict";
          Xe();
          A = _;
        });
        var It, At, Bt = E(() => {
          "use strict";
          It = (e, t) => {
            let n = typeof document < "u" ? document.createElement("canvas") : new OffscreenCanvas(1, 1);
            n.width = e.dims[3], n.height = e.dims[2];
            let o = n.getContext("2d");
            if (o != null) {
              let r, i;
              t?.tensorLayout !== void 0 && t.tensorLayout === "NHWC" ? (r = e.dims[2], i = e.dims[3]) : (r = e.dims[3], i = e.dims[2]);
              let s = t?.format !== void 0 ? t.format : "RGB", a = t?.norm, u, f;
              a === void 0 || a.mean === void 0 ? u = [255, 255, 255, 255] : typeof a.mean == "number" ? u = [a.mean, a.mean, a.mean, a.mean] : (u = [a.mean[0], a.mean[1], a.mean[2], 0], a.mean[3] !== void 0 && (u[3] = a.mean[3])), a === void 0 || a.bias === void 0 ? f = [0, 0, 0, 0] : typeof a.bias == "number" ? f = [a.bias, a.bias, a.bias, a.bias] : (f = [a.bias[0], a.bias[1], a.bias[2], 0], a.bias[3] !== void 0 && (f[3] = a.bias[3]));
              let l = i * r, c = 0, d = l, p = l * 2, h = -1;
              s === "RGBA" ? (c = 0, d = l, p = l * 2, h = l * 3) : s === "RGB" ? (c = 0, d = l, p = l * 2) : s === "RBG" && (c = 0, p = l, d = l * 2);
              for (let y = 0; y < i; y++) for (let B = 0; B < r; B++) {
                let m = (e.data[c++] - f[0]) * u[0], w = (e.data[d++] - f[1]) * u[1], O = (e.data[p++] - f[2]) * u[2], g = h === -1 ? 255 : (e.data[h++] - f[3]) * u[3];
                o.fillStyle = "rgba(" + m + "," + w + "," + O + "," + g + ")", o.fillRect(B, y, 1, 1);
              }
              if ("toDataURL" in n) return n.toDataURL();
              throw new Error("toDataURL is not supported");
            } else throw new Error("Can not access image data");
          }, At = (e, t) => {
            let n = typeof document < "u" ? document.createElement("canvas").getContext("2d") : new OffscreenCanvas(1, 1).getContext("2d"), o;
            if (n != null) {
              let r, i, s;
              t?.tensorLayout !== void 0 && t.tensorLayout === "NHWC" ? (r = e.dims[2], i = e.dims[1], s = e.dims[3]) : (r = e.dims[3], i = e.dims[2], s = e.dims[1]);
              let a = t !== void 0 && t.format !== void 0 ? t.format : "RGB", u = t?.norm, f, l;
              u === void 0 || u.mean === void 0 ? f = [255, 255, 255, 255] : typeof u.mean == "number" ? f = [u.mean, u.mean, u.mean, u.mean] : (f = [u.mean[0], u.mean[1], u.mean[2], 255], u.mean[3] !== void 0 && (f[3] = u.mean[3])), u === void 0 || u.bias === void 0 ? l = [0, 0, 0, 0] : typeof u.bias == "number" ? l = [u.bias, u.bias, u.bias, u.bias] : (l = [u.bias[0], u.bias[1], u.bias[2], 0], u.bias[3] !== void 0 && (l[3] = u.bias[3]));
              let c = i * r;
              if (t !== void 0 && (t.format !== void 0 && s === 4 && t.format !== "RGBA" || s === 3 && t.format !== "RGB" && t.format !== "BGR")) throw new Error("Tensor format doesn't match input tensor dims");
              let d = 4, p = 0, h = 1, y = 2, B = 3, m = 0, w = c, O = c * 2, g = -1;
              a === "RGBA" ? (m = 0, w = c, O = c * 2, g = c * 3) : a === "RGB" ? (m = 0, w = c, O = c * 2) : a === "RBG" && (m = 0, O = c, w = c * 2), o = n.createImageData(r, i);
              for (let T = 0; T < i * r; p += d, h += d, y += d, B += d, T++) o.data[p] = (e.data[m++] - l[0]) * f[0], o.data[h] = (e.data[w++] - l[1]) * f[1], o.data[y] = (e.data[O++] - l[2]) * f[2], o.data[B] = g === -1 ? 255 : (e.data[g++] - l[3]) * f[3];
            } else throw new Error("Can not access image data");
            return o;
          };
        });
        var Ke, Ot, Lt, Pt, _t, Dt, Ut = E(() => {
          "use strict";
          Ie();
          Ke = (e, t) => {
            if (e === void 0) throw new Error("Image buffer must be defined");
            if (t.height === void 0 || t.width === void 0) throw new Error("Image height and width must be defined");
            if (t.tensorLayout === "NHWC") throw new Error("NHWC Tensor layout is not supported yet");
            let { height: n, width: o } = t, r = t.norm ?? { mean: 255, bias: 0 }, i, s;
            typeof r.mean == "number" ? i = [r.mean, r.mean, r.mean, r.mean] : i = [r.mean[0], r.mean[1], r.mean[2], r.mean[3] ?? 255], typeof r.bias == "number" ? s = [r.bias, r.bias, r.bias, r.bias] : s = [r.bias[0], r.bias[1], r.bias[2], r.bias[3] ?? 0];
            let a = t.format !== void 0 ? t.format : "RGBA", u = t.tensorFormat !== void 0 && t.tensorFormat !== void 0 ? t.tensorFormat : "RGB", f = n * o, l = u === "RGBA" ? new Float32Array(f * 4) : new Float32Array(f * 3), c = 4, d = 0, p = 1, h = 2, y = 3, B = 0, m = f, w = f * 2, O = -1;
            a === "RGB" && (c = 3, d = 0, p = 1, h = 2, y = -1), u === "RGBA" ? O = f * 3 : u === "RBG" ? (B = 0, w = f, m = f * 2) : u === "BGR" && (w = 0, m = f, B = f * 2);
            for (let T = 0; T < f; T++, d += c, h += c, p += c, y += c) l[B++] = (e[d] + s[0]) / i[0], l[m++] = (e[p] + s[1]) / i[1], l[w++] = (e[h] + s[2]) / i[2], O !== -1 && y !== -1 && (l[O++] = (e[y] + s[3]) / i[3]);
            return u === "RGBA" ? new x("float32", l, [1, 4, n, o]) : new x("float32", l, [1, 3, n, o]);
          }, Ot = async (e, t) => {
            let n = typeof HTMLImageElement < "u" && e instanceof HTMLImageElement, o = typeof ImageData < "u" && e instanceof ImageData, r = typeof ImageBitmap < "u" && e instanceof ImageBitmap, i = typeof e == "string", s, a = t ?? {}, u = () => {
              if (typeof document < "u") return document.createElement("canvas");
              if (typeof OffscreenCanvas < "u") return new OffscreenCanvas(1, 1);
              throw new Error("Canvas is not supported");
            }, f = (l) => typeof HTMLCanvasElement < "u" && l instanceof HTMLCanvasElement || l instanceof OffscreenCanvas ? l.getContext("2d") : null;
            if (n) {
              let l = u();
              l.width = e.width, l.height = e.height;
              let c = f(l);
              if (c != null) {
                let d = e.height, p = e.width;
                if (t !== void 0 && t.resizedHeight !== void 0 && t.resizedWidth !== void 0 && (d = t.resizedHeight, p = t.resizedWidth), t !== void 0) {
                  if (a = t, t.tensorFormat !== void 0) throw new Error("Image input config format must be RGBA for HTMLImageElement");
                  a.tensorFormat = "RGBA", a.height = d, a.width = p;
                } else a.tensorFormat = "RGBA", a.height = d, a.width = p;
                c.drawImage(e, 0, 0), s = c.getImageData(0, 0, p, d).data;
              } else throw new Error("Can not access image data");
            } else if (o) {
              let l, c;
              if (t !== void 0 && t.resizedWidth !== void 0 && t.resizedHeight !== void 0 ? (l = t.resizedHeight, c = t.resizedWidth) : (l = e.height, c = e.width), t !== void 0 && (a = t), a.format = "RGBA", a.height = l, a.width = c, t !== void 0) {
                let d = u();
                d.width = c, d.height = l;
                let p = f(d);
                if (p != null) p.putImageData(e, 0, 0), s = p.getImageData(0, 0, c, l).data;
                else throw new Error("Can not access image data");
              } else s = e.data;
            } else if (r) {
              if (t === void 0) throw new Error("Please provide image config with format for Imagebitmap");
              let l = u();
              l.width = e.width, l.height = e.height;
              let c = f(l);
              if (c != null) {
                let d = e.height, p = e.width;
                return c.drawImage(e, 0, 0, p, d), s = c.getImageData(0, 0, p, d).data, a.height = d, a.width = p, Ke(s, a);
              } else throw new Error("Can not access image data");
            } else {
              if (i) return new Promise((l, c) => {
                let d = u(), p = f(d);
                if (!e || !p) return c();
                let h = new Image();
                h.crossOrigin = "Anonymous", h.src = e, h.onload = () => {
                  d.width = h.width, d.height = h.height, p.drawImage(h, 0, 0, d.width, d.height);
                  let y = p.getImageData(0, 0, d.width, d.height);
                  a.height = d.height, a.width = d.width, l(Ke(y.data, a));
                };
              });
              throw new Error("Input data provided is not supported - aborted tensor creation");
            }
            if (s !== void 0) return Ke(s, a);
            throw new Error("Input data provided is not supported - aborted tensor creation");
          }, Lt = (e, t) => {
            let { width: n, height: o, download: r, dispose: i } = t, s = [1, o, n, 4];
            return new x({ location: "texture", type: "float32", texture: e, dims: s, download: r, dispose: i });
          }, Pt = (e, t) => {
            let { dataType: n, dims: o, download: r, dispose: i } = t;
            return new x({ location: "gpu-buffer", type: n ?? "float32", gpuBuffer: e, dims: o, download: r, dispose: i });
          }, _t = (e, t) => {
            let { dataType: n, dims: o, download: r, dispose: i } = t;
            return new x({ location: "ml-tensor", type: n ?? "float32", mlTensor: e, dims: o, download: r, dispose: i });
          }, Dt = (e, t, n) => new x({ location: "cpu-pinned", type: e, data: t, dims: n ?? [t.length] });
        });
        var Q, me, xt, vt, Ct = E(() => {
          "use strict";
          Q = /* @__PURE__ */ new Map([["float32", Float32Array], ["uint8", Uint8Array], ["int8", Int8Array], ["uint16", Uint16Array], ["int16", Int16Array], ["int32", Int32Array], ["bool", Uint8Array], ["float64", Float64Array], ["uint32", Uint32Array], ["int4", Uint8Array], ["uint4", Uint8Array]]), me = /* @__PURE__ */ new Map([[Float32Array, "float32"], [Uint8Array, "uint8"], [Int8Array, "int8"], [Uint16Array, "uint16"], [Int16Array, "int16"], [Int32Array, "int32"], [Float64Array, "float64"], [Uint32Array, "uint32"]]), xt = false, vt = () => {
            if (!xt) {
              xt = true;
              let e = typeof BigInt64Array < "u" && BigInt64Array.from, t = typeof BigUint64Array < "u" && BigUint64Array.from, n = globalThis.Float16Array, o = typeof n < "u" && n.from;
              e && (Q.set("int64", BigInt64Array), me.set(BigInt64Array, "int64")), t && (Q.set("uint64", BigUint64Array), me.set(BigUint64Array, "uint64")), o ? (Q.set("float16", n), me.set(n, "float16")) : Q.set("float16", Uint16Array);
            }
          };
        });
        var Mt, Rt, Nt = E(() => {
          "use strict";
          Ie();
          Mt = (e) => {
            let t = 1;
            for (let n = 0; n < e.length; n++) {
              let o = e[n];
              if (typeof o != "number" || !Number.isSafeInteger(o)) throw new TypeError(`dims[${n}] must be an integer, got: ${o}`);
              if (o < 0) throw new RangeError(`dims[${n}] must be a non-negative integer, got: ${o}`);
              t *= o;
            }
            return t;
          }, Rt = (e, t) => {
            switch (e.location) {
              case "cpu":
                return new x(e.type, e.data, t);
              case "cpu-pinned":
                return new x({ location: "cpu-pinned", data: e.data, type: e.type, dims: t });
              case "texture":
                return new x({ location: "texture", texture: e.texture, type: e.type, dims: t });
              case "gpu-buffer":
                return new x({ location: "gpu-buffer", gpuBuffer: e.gpuBuffer, type: e.type, dims: t });
              case "ml-tensor":
                return new x({ location: "ml-tensor", mlTensor: e.mlTensor, type: e.type, dims: t });
              default:
                throw new Error(`tensorReshape: tensor location ${e.location} is not supported`);
            }
          };
        });
        var x, Ie = E(() => {
          "use strict";
          Bt();
          Ut();
          Ct();
          Nt();
          x = class {
            constructor(t, n, o) {
              vt();
              let r, i;
              if (typeof t == "object" && "location" in t) switch (this.dataLocation = t.location, r = t.type, i = t.dims, t.location) {
                case "cpu-pinned": {
                  let a = Q.get(r);
                  if (!a) throw new TypeError(`unsupported type "${r}" to create tensor from pinned buffer`);
                  if (!(t.data instanceof a)) throw new TypeError(`buffer should be of type ${a.name}`);
                  this.cpuData = t.data;
                  break;
                }
                case "texture": {
                  if (r !== "float32") throw new TypeError(`unsupported type "${r}" to create tensor from texture`);
                  this.gpuTextureData = t.texture, this.downloader = t.download, this.disposer = t.dispose;
                  break;
                }
                case "gpu-buffer": {
                  if (r !== "float32" && r !== "float16" && r !== "int32" && r !== "int64" && r !== "uint32" && r !== "uint8" && r !== "bool" && r !== "uint4" && r !== "int4") throw new TypeError(`unsupported type "${r}" to create tensor from gpu buffer`);
                  this.gpuBufferData = t.gpuBuffer, this.downloader = t.download, this.disposer = t.dispose;
                  break;
                }
                case "ml-tensor": {
                  if (r !== "float32" && r !== "float16" && r !== "int32" && r !== "int64" && r !== "uint32" && r !== "uint64" && r !== "int8" && r !== "uint8" && r !== "bool" && r !== "uint4" && r !== "int4") throw new TypeError(`unsupported type "${r}" to create tensor from MLTensor`);
                  this.mlTensorData = t.mlTensor, this.downloader = t.download, this.disposer = t.dispose;
                  break;
                }
                default:
                  throw new Error(`Tensor constructor: unsupported location '${this.dataLocation}'`);
              }
              else {
                let a, u;
                if (typeof t == "string") if (r = t, u = o, t === "string") {
                  if (!Array.isArray(n)) throw new TypeError("A string tensor's data must be a string array.");
                  a = n;
                } else {
                  let f = Q.get(t);
                  if (f === void 0) throw new TypeError(`Unsupported tensor type: ${t}.`);
                  if (Array.isArray(n)) {
                    if (t === "float16" && f === Uint16Array || t === "uint4" || t === "int4") throw new TypeError(`Creating a ${t} tensor from number array is not supported. Please use ${f.name} as data.`);
                    t === "uint64" || t === "int64" ? a = f.from(n, BigInt) : a = f.from(n);
                  } else if (n instanceof f) a = n;
                  else if (n instanceof Uint8ClampedArray) if (t === "uint8") a = Uint8Array.from(n);
                  else throw new TypeError("A Uint8ClampedArray tensor's data must be type of uint8");
                  else if (t === "float16" && n instanceof Uint16Array && f !== Uint16Array) a = new globalThis.Float16Array(n.buffer, n.byteOffset, n.length);
                  else throw new TypeError(`A ${r} tensor's data must be type of ${f}`);
                }
                else if (u = n, Array.isArray(t)) {
                  if (t.length === 0) throw new TypeError("Tensor type cannot be inferred from an empty array.");
                  let f = typeof t[0];
                  if (f === "string") r = "string", a = t;
                  else if (f === "boolean") r = "bool", a = Uint8Array.from(t);
                  else throw new TypeError(`Invalid element type of data array: ${f}.`);
                } else if (t instanceof Uint8ClampedArray) r = "uint8", a = Uint8Array.from(t);
                else {
                  let f = me.get(t.constructor);
                  if (f === void 0) throw new TypeError(`Unsupported type for tensor data: ${t.constructor}.`);
                  r = f, a = t;
                }
                if (u === void 0) u = [a.length];
                else if (!Array.isArray(u)) throw new TypeError("A tensor's dims must be a number array");
                i = u, this.cpuData = a, this.dataLocation = "cpu";
              }
              let s = Mt(i);
              if (this.cpuData && s !== this.cpuData.length && !((r === "uint4" || r === "int4") && Math.ceil(s / 2) === this.cpuData.length)) throw new Error(`Tensor's size(${s}) does not match data length(${this.cpuData.length}).`);
              this.type = r, this.dims = i, this.size = s;
            }
            static async fromImage(t, n) {
              return Ot(t, n);
            }
            static fromTexture(t, n) {
              return Lt(t, n);
            }
            static fromGpuBuffer(t, n) {
              return Pt(t, n);
            }
            static fromMLTensor(t, n) {
              return _t(t, n);
            }
            static fromPinnedBuffer(t, n, o) {
              return Dt(t, n, o);
            }
            toDataURL(t) {
              return It(this, t);
            }
            toImageData(t) {
              return At(this, t);
            }
            get data() {
              if (this.ensureValid(), !this.cpuData) throw new Error("The data is not on CPU. Use `getData()` to download GPU data to CPU, or use `texture` or `gpuBuffer` property to access the GPU data directly.");
              return this.cpuData;
            }
            get location() {
              return this.dataLocation;
            }
            get texture() {
              if (this.ensureValid(), !this.gpuTextureData) throw new Error("The data is not stored as a WebGL texture.");
              return this.gpuTextureData;
            }
            get gpuBuffer() {
              if (this.ensureValid(), !this.gpuBufferData) throw new Error("The data is not stored as a WebGPU buffer.");
              return this.gpuBufferData;
            }
            get mlTensor() {
              if (this.ensureValid(), !this.mlTensorData) throw new Error("The data is not stored as a WebNN MLTensor.");
              return this.mlTensorData;
            }
            async getData(t) {
              switch (this.ensureValid(), this.dataLocation) {
                case "cpu":
                case "cpu-pinned":
                  return this.data;
                case "texture":
                case "gpu-buffer":
                case "ml-tensor": {
                  if (!this.downloader) throw new Error("The current tensor is not created with a specified data downloader.");
                  if (this.isDownloading) throw new Error("The current tensor is being downloaded.");
                  try {
                    this.isDownloading = true;
                    let n = await this.downloader();
                    return this.downloader = void 0, this.dataLocation = "cpu", this.cpuData = n, t && this.disposer && (this.disposer(), this.disposer = void 0), n;
                  } finally {
                    this.isDownloading = false;
                  }
                }
                default:
                  throw new Error(`cannot get data from location: ${this.dataLocation}`);
              }
            }
            dispose() {
              if (this.isDownloading) throw new Error("The current tensor is being downloaded.");
              this.disposer && (this.disposer(), this.disposer = void 0), this.cpuData = void 0, this.gpuTextureData = void 0, this.gpuBufferData = void 0, this.mlTensorData = void 0, this.downloader = void 0, this.isDownloading = void 0, this.dataLocation = "none";
            }
            ensureValid() {
              if (this.dataLocation === "none") throw new Error("The tensor is disposed.");
            }
            reshape(t) {
              if (this.ensureValid(), this.downloader || this.disposer) throw new Error("Cannot reshape a tensor that owns GPU resource.");
              return Rt(this, t);
            }
          };
        });
        var k, Qe = E(() => {
          "use strict";
          Ie();
          k = x;
        });
        var et, Ft, J, q, Y, Z, tt = E(() => {
          "use strict";
          Xe();
          et = (e, t) => {
            (typeof _.trace > "u" ? !_.wasm.trace : !_.trace) || console.timeStamp(`${e}::ORT::${t}`);
          }, Ft = (e, t) => {
            let n = new Error().stack?.split(/\r\n|\r|\n/g) || [], o = false;
            for (let r = 0; r < n.length; r++) {
              if (o && !n[r].includes("TRACE_FUNC")) {
                let i = `FUNC_${e}::${n[r].trim().split(" ")[1]}`;
                t && (i += `::${t}`), et("CPU", i);
                return;
              }
              n[r].includes("TRACE_FUNC") && (o = true);
            }
          }, J = (e) => {
            (typeof _.trace > "u" ? !_.wasm.trace : !_.trace) || Ft("BEGIN", e);
          }, q = (e) => {
            (typeof _.trace > "u" ? !_.wasm.trace : !_.trace) || Ft("END", e);
          }, Y = (e) => {
            (typeof _.trace > "u" ? !_.wasm.trace : !_.trace) || console.time(`ORT::${e}`);
          }, Z = (e) => {
            (typeof _.trace > "u" ? !_.wasm.trace : !_.trace) || console.timeEnd(`ORT::${e}`);
          };
        });
        var Ae, kt = E(() => {
          "use strict";
          Ze();
          Qe();
          tt();
          Ae = class e {
            constructor(t) {
              this.handler = t;
            }
            async run(t, n, o) {
              J(), Y("InferenceSession.run");
              let r = {}, i = {};
              if (typeof t != "object" || t === null || t instanceof k || Array.isArray(t)) throw new TypeError("'feeds' must be an object that use input names as keys and OnnxValue as corresponding values.");
              let s = true;
              if (typeof n == "object") {
                if (n === null) throw new TypeError("Unexpected argument[1]: cannot be null.");
                if (n instanceof k) throw new TypeError("'fetches' cannot be a Tensor");
                if (Array.isArray(n)) {
                  if (n.length === 0) throw new TypeError("'fetches' cannot be an empty array.");
                  s = false;
                  for (let f of n) {
                    if (typeof f != "string") throw new TypeError("'fetches' must be a string array or an object.");
                    if (this.outputNames.indexOf(f) === -1) throw new RangeError(`'fetches' contains invalid output name: ${f}.`);
                    r[f] = null;
                  }
                  if (typeof o == "object" && o !== null) i = o;
                  else if (typeof o < "u") throw new TypeError("'options' must be an object.");
                } else {
                  let f = false, l = Object.getOwnPropertyNames(n);
                  for (let c of this.outputNames) if (l.indexOf(c) !== -1) {
                    let d = n[c];
                    (d === null || d instanceof k) && (f = true, s = false, r[c] = d);
                  }
                  if (f) {
                    if (typeof o == "object" && o !== null) i = o;
                    else if (typeof o < "u") throw new TypeError("'options' must be an object.");
                  } else i = n;
                }
              } else if (typeof n < "u") throw new TypeError("Unexpected argument[1]: must be 'fetches' or 'options'.");
              for (let f of this.inputNames) if (typeof t[f] > "u") throw new Error(`input '${f}' is missing in 'feeds'.`);
              if (s) for (let f of this.outputNames) r[f] = null;
              let a = await this.handler.run(t, r, i), u = {};
              for (let f in a) if (Object.hasOwnProperty.call(a, f)) {
                let l = a[f];
                l instanceof k ? u[f] = l : u[f] = new k(l.type, l.data, l.dims);
              }
              return Z("InferenceSession.run"), q(), u;
            }
            async release() {
              return this.handler.dispose();
            }
            static async create(t, n, o, r) {
              J(), Y("InferenceSession.create");
              let i, s = {};
              if (typeof t == "string") {
                if (i = t, typeof n == "object" && n !== null) s = n;
                else if (typeof n < "u") throw new TypeError("'options' must be an object.");
              } else if (t instanceof Uint8Array) {
                if (i = t, typeof n == "object" && n !== null) s = n;
                else if (typeof n < "u") throw new TypeError("'options' must be an object.");
              } else if (t instanceof ArrayBuffer || typeof SharedArrayBuffer < "u" && t instanceof SharedArrayBuffer) {
                let l = t, c = 0, d = t.byteLength;
                if (typeof n == "object" && n !== null) s = n;
                else if (typeof n == "number") {
                  if (c = n, !Number.isSafeInteger(c)) throw new RangeError("'byteOffset' must be an integer.");
                  if (c < 0 || c >= l.byteLength) throw new RangeError(`'byteOffset' is out of range [0, ${l.byteLength}).`);
                  if (d = t.byteLength - c, typeof o == "number") {
                    if (d = o, !Number.isSafeInteger(d)) throw new RangeError("'byteLength' must be an integer.");
                    if (d <= 0 || c + d > l.byteLength) throw new RangeError(`'byteLength' is out of range (0, ${l.byteLength - c}].`);
                    if (typeof r == "object" && r !== null) s = r;
                    else if (typeof r < "u") throw new TypeError("'options' must be an object.");
                  } else if (typeof o < "u") throw new TypeError("'byteLength' must be a number.");
                } else if (typeof n < "u") throw new TypeError("'options' must be an object.");
                i = new Uint8Array(l, c, d);
              } else throw new TypeError("Unexpected argument[0]: must be 'path' or 'buffer'.");
              let [a, u] = await bt(s), f = await a.createInferenceSessionHandler(i, u);
              return Z("InferenceSession.create"), q(), new e(f);
            }
            startProfiling() {
              this.handler.startProfiling();
            }
            endProfiling() {
              this.handler.endProfiling();
            }
            get inputNames() {
              return this.handler.inputNames;
            }
            get outputNames() {
              return this.handler.outputNames;
            }
            get inputMetadata() {
              return this.handler.inputMetadata;
            }
            get outputMetadata() {
              return this.handler.outputMetadata;
            }
          };
        });
        var Wt, Gt = E(() => {
          "use strict";
          kt();
          Wt = Ae;
        });
        var $t = E(() => {
          "use strict";
        });
        var zt = E(() => {
          "use strict";
        });
        var Ht = E(() => {
          "use strict";
        });
        var jt = E(() => {
          "use strict";
        });
        var nt = {};
        Se(nt, { InferenceSession: () => Wt, TRACE: () => et, TRACE_EVENT_BEGIN: () => Y, TRACE_EVENT_END: () => Z, TRACE_FUNC_BEGIN: () => J, TRACE_FUNC_END: () => q, Tensor: () => k, env: () => A, registerBackend: () => se });
        var X = E(() => {
          "use strict";
          yt();
          Tt();
          Gt();
          Qe();
          $t();
          zt();
          tt();
          Ht();
          jt();
        });
        var Be = E(() => {
          "use strict";
        });
        var Yt = {};
        Se(Yt, { default: () => Un });
        var Jt, qt, Un, Zt = E(() => {
          "use strict";
          rt();
          ee();
          Oe();
          Jt = "ort-wasm-proxy-worker", qt = globalThis.self?.name === Jt;
          qt && (self.onmessage = (e) => {
            let { type: t, in: n } = e.data;
            try {
              switch (t) {
                case "init-wasm":
                  Le(n.wasm).then(() => {
                    Pe(n).then(() => {
                      postMessage({ type: t });
                    }, (o) => {
                      postMessage({ type: t, err: o });
                    });
                  }, (o) => {
                    postMessage({ type: t, err: o });
                  });
                  break;
                case "init-ep": {
                  let { epName: o, env: r } = n;
                  _e(r, o).then(() => {
                    postMessage({ type: t });
                  }, (i) => {
                    postMessage({ type: t, err: i });
                  });
                  break;
                }
                case "copy-from": {
                  let { buffer: o } = n, r = we(o);
                  postMessage({ type: t, out: r });
                  break;
                }
                case "create": {
                  let { model: o, options: r } = n;
                  De(o, r).then((i) => {
                    postMessage({ type: t, out: i });
                  }, (i) => {
                    postMessage({ type: t, err: i });
                  });
                  break;
                }
                case "release":
                  Ue(n), postMessage({ type: t });
                  break;
                case "run": {
                  let { sessionId: o, inputIndices: r, inputs: i, outputIndices: s, options: a } = n;
                  xe(o, r, i, s, new Array(s.length).fill(null), a).then((u) => {
                    u.some((f) => f[3] !== "cpu") ? postMessage({ type: t, err: "Proxy does not support non-cpu tensor location." }) : postMessage({ type: t, out: u }, Ce([...i, ...u]));
                  }, (u) => {
                    postMessage({ type: t, err: u });
                  });
                  break;
                }
                case "end-profiling":
                  ve(n), postMessage({ type: t });
                  break;
                default:
              }
            } catch (o) {
              postMessage({ type: t, err: o });
            }
          });
          Un = qt ? null : (e) => new Worker(e ?? R, { type: "classic", name: Jt });
        });
        var xn, vn, R, Me, ot, Cn, Mn, Qt, Rn, Xt, en, Kt, tn, Oe = E(() => {
          "use strict";
          Be();
          xn = typeof location > "u" ? void 0 : location.origin, vn = () => {
            if (true) return typeof document < "u" ? document.currentScript?.src : typeof self < "u" ? self.location?.href : void 0;
          }, R = vn(), Me = () => {
            if (R && !R.startsWith("blob:")) return R.substring(0, R.lastIndexOf("/") + 1);
          }, ot = (e, t) => {
            try {
              let n = t ?? R;
              return (n ? new URL(e, n) : new URL(e)).origin === xn;
            } catch {
              return false;
            }
          }, Cn = (e, t) => {
            let n = t ?? R;
            try {
              return (n ? new URL(e, n) : new URL(e)).href;
            } catch {
              return;
            }
          }, Mn = (e, t) => `${t ?? "./"}${e}`, Qt = async (e) => {
            let n = await (await fetch(e, { credentials: "same-origin" })).blob();
            return URL.createObjectURL(n);
          }, Rn = async (e) => (await import(
            /*webpackIgnore:true*/
            /*@vite-ignore*/
            e
          )).default, Xt = (Zt(), Ye(Yt)).default, en = async () => {
            if (!R) throw new Error("Failed to load proxy worker: cannot determine the script source URL.");
            if (ot(R)) return [void 0, Xt()];
            let e = await Qt(R);
            return [e, Xt(e)];
          }, Kt = void 0, tn = async (e, t, n, o) => {
            let r = Kt && !(e || t);
            if (r) if (R) r = ot(R) || o && !n;
            else if (o && !n) r = true;
            else throw new Error("cannot determine the script source URL.");
            if (r) return [void 0, Kt];
            {
              let i = "ort-wasm-simd-threaded.mjs", s = e ?? Cn(i, t), a = n && s && !ot(s, t), u = a ? await Qt(s) : s ?? Mn(i, t);
              return [a ? u : void 0, await Rn(u)];
            }
          };
        });
        var st, it, Re, nn, Nn, Fn, kn, Le, I, ee = E(() => {
          "use strict";
          Oe();
          it = false, Re = false, nn = false, Nn = () => {
            if (typeof SharedArrayBuffer > "u") return false;
            try {
              return typeof MessageChannel < "u" && new MessageChannel().port1.postMessage(new SharedArrayBuffer(1)), WebAssembly.validate(new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0, 1, 4, 1, 96, 0, 0, 3, 2, 1, 0, 5, 4, 1, 3, 1, 1, 10, 11, 1, 9, 0, 65, 0, 254, 16, 2, 0, 26, 11]));
            } catch {
              return false;
            }
          }, Fn = () => {
            try {
              return WebAssembly.validate(new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0, 1, 4, 1, 96, 0, 0, 3, 2, 1, 0, 10, 30, 1, 28, 0, 65, 0, 253, 15, 253, 12, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 253, 186, 1, 26, 11]));
            } catch {
              return false;
            }
          }, kn = () => {
            try {
              return WebAssembly.validate(new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0, 1, 5, 1, 96, 0, 1, 123, 3, 2, 1, 0, 10, 19, 1, 17, 0, 65, 1, 253, 15, 65, 2, 253, 15, 65, 3, 253, 15, 253, 147, 2, 11]));
            } catch {
              return false;
            }
          }, Le = async (e) => {
            if (it) return Promise.resolve();
            if (Re) throw new Error("multiple calls to 'initializeWebAssembly()' detected.");
            if (nn) throw new Error("previous call to 'initializeWebAssembly()' failed.");
            Re = true;
            let t = e.initTimeout, n = e.numThreads;
            if (e.simd !== false) {
              if (e.simd === "relaxed") {
                if (!kn()) throw new Error("Relaxed WebAssembly SIMD is not supported in the current environment.");
              } else if (!Fn()) throw new Error("WebAssembly SIMD is not supported in the current environment.");
            }
            let o = Nn();
            n > 1 && !o && (typeof self < "u" && !self.crossOriginIsolated && console.warn("env.wasm.numThreads is set to " + n + ", but this will not work unless you enable crossOriginIsolated mode. See https://web.dev/cross-origin-isolation-guide/ for more info."), console.warn("WebAssembly multi-threading is not supported in the current environment. Falling back to single-threading."), e.numThreads = n = 1);
            let r = e.wasmPaths, i = typeof r == "string" ? r : void 0, s = r?.mjs, a = s?.href ?? s, u = r?.wasm, f = u?.href ?? u, l = e.wasmBinary, [c, d] = await tn(a, i, n > 1, !!l || !!f), p = false, h = [];
            if (t > 0 && h.push(new Promise((y) => {
              setTimeout(() => {
                p = true, y();
              }, t);
            })), h.push(new Promise((y, B) => {
              let m = { numThreads: n };
              if (l) m.wasmBinary = l, m.locateFile = (w) => w;
              else if (f || i) m.locateFile = (w) => f ?? i + w;
              else if (a && a.indexOf("blob:") !== 0) m.locateFile = (w) => new URL(w, a).href;
              else if (c) {
                let w = Me();
                w && (m.locateFile = (O) => w + O);
              }
              d(m).then((w) => {
                Re = false, it = true, st = w, y(), c && URL.revokeObjectURL(c);
              }, (w) => {
                Re = false, nn = true, B(w);
              });
            })), await Promise.race(h), p) throw new Error(`WebAssembly backend initializing failed due to timeout: ${t}ms`);
          }, I = () => {
            if (it && st) return st;
            throw new Error("WebAssembly is not initialized yet.");
          };
        });
        var N, he, S, Ne = E(() => {
          "use strict";
          ee();
          N = (e, t) => {
            let n = I(), o = n.lengthBytesUTF8(e) + 1, r = n._malloc(o);
            return n.stringToUTF8(e, r, o), t.push(r), r;
          }, he = (e, t, n, o) => {
            if (typeof e == "object" && e !== null) {
              if (n.has(e)) throw new Error("Circular reference in options");
              n.add(e);
            }
            Object.entries(e).forEach(([r, i]) => {
              let s = t ? t + r : r;
              if (typeof i == "object") he(i, s + ".", n, o);
              else if (typeof i == "string" || typeof i == "number") o(s, i.toString());
              else if (typeof i == "boolean") o(s, i ? "1" : "0");
              else throw new Error(`Can't handle extra config type: ${typeof i}`);
            });
          }, S = (e) => {
            let t = I(), n = t.stackSave();
            try {
              let o = t.PTR_SIZE, r = t.stackAlloc(2 * o);
              t._OrtGetLastError(r, r + o);
              let i = Number(t.getValue(r, o === 4 ? "i32" : "i64")), s = t.getValue(r + o, "*"), a = s ? t.UTF8ToString(s) : "";
              throw new Error(`${e} ERROR_CODE: ${i}, ERROR_MESSAGE: ${a}`);
            } finally {
              t.stackRestore(n);
            }
          };
        });
        var rn, on = E(() => {
          "use strict";
          ee();
          Ne();
          rn = (e) => {
            let t = I(), n = 0, o = [], r = e || {};
            try {
              if (e?.logSeverityLevel === void 0) r.logSeverityLevel = 2;
              else if (typeof e.logSeverityLevel != "number" || !Number.isInteger(e.logSeverityLevel) || e.logSeverityLevel < 0 || e.logSeverityLevel > 4) throw new Error(`log severity level is not valid: ${e.logSeverityLevel}`);
              if (e?.logVerbosityLevel === void 0) r.logVerbosityLevel = 0;
              else if (typeof e.logVerbosityLevel != "number" || !Number.isInteger(e.logVerbosityLevel)) throw new Error(`log verbosity level is not valid: ${e.logVerbosityLevel}`);
              e?.terminate === void 0 && (r.terminate = false);
              let i = 0;
              return e?.tag !== void 0 && (i = N(e.tag, o)), n = t._OrtCreateRunOptions(r.logSeverityLevel, r.logVerbosityLevel, !!r.terminate, i), n === 0 && S("Can't create run options."), e?.extra !== void 0 && he(e.extra, "", /* @__PURE__ */ new WeakSet(), (s, a) => {
                let u = N(s, o), f = N(a, o);
                t._OrtAddRunConfigEntry(n, u, f) !== 0 && S(`Can't set a run config entry: ${s} - ${a}.`);
              }), [n, o];
            } catch (i) {
              throw n !== 0 && t._OrtReleaseRunOptions(n), o.forEach((s) => t._free(s)), i;
            }
          };
        });
        var Wn, Gn, $n, ie, zn, sn, an = E(() => {
          "use strict";
          ee();
          Ne();
          Wn = (e) => {
            switch (e) {
              case "disabled":
                return 0;
              case "basic":
                return 1;
              case "extended":
                return 2;
              case "layout":
                return 3;
              case "all":
                return 99;
              default:
                throw new Error(`unsupported graph optimization level: ${e}`);
            }
          }, Gn = (e) => {
            switch (e) {
              case "sequential":
                return 0;
              case "parallel":
                return 1;
              default:
                throw new Error(`unsupported execution mode: ${e}`);
            }
          }, $n = (e) => {
            e.extra || (e.extra = {}), e.extra.session || (e.extra.session = {});
            let t = e.extra.session;
            t.use_ort_model_bytes_directly || (t.use_ort_model_bytes_directly = "1"), e.executionProviders && e.executionProviders.some((n) => (typeof n == "string" ? n : n.name) === "webgpu") && (e.enableMemPattern = false);
          }, ie = (e, t, n, o) => {
            let r = N(t, o), i = N(n, o);
            I()._OrtAddSessionConfigEntry(e, r, i) !== 0 && S(`Can't set a session config entry: ${t} - ${n}.`);
          }, zn = async (e, t, n) => {
            let o = t.executionProviders;
            for (let r of o) {
              let i = typeof r == "string" ? r : r.name, s = [];
              switch (i) {
                case "webnn":
                  if (i = "WEBNN", ie(e, "session.disable_quant_qdq", "1", n), ie(e, "session.disable_qdq_constant_folding", "1", n), typeof r != "string") {
                    let d = r?.deviceType;
                    d && ie(e, "deviceType", d, n);
                  }
                  break;
                case "webgpu":
                  if (i = "JS", typeof r != "string") {
                    let c = r;
                    if (c?.preferredLayout) {
                      if (c.preferredLayout !== "NCHW" && c.preferredLayout !== "NHWC") throw new Error(`preferredLayout must be either 'NCHW' or 'NHWC': ${c.preferredLayout}`);
                      ie(e, "preferredLayout", c.preferredLayout, n);
                    }
                  }
                  break;
                case "wasm":
                case "cpu":
                  continue;
                default:
                  throw new Error(`not supported execution provider: ${i}`);
              }
              let a = N(i, n), u = s.length, f = 0, l = 0;
              if (u > 0) {
                f = I()._malloc(u * I().PTR_SIZE), n.push(f), l = I()._malloc(u * I().PTR_SIZE), n.push(l);
                for (let c = 0; c < u; c++) I().setValue(f + c * I().PTR_SIZE, s[c][0], "*"), I().setValue(l + c * I().PTR_SIZE, s[c][1], "*");
              }
              await I()._OrtAppendExecutionProvider(e, a, f, l, u) !== 0 && S(`Can't append execution provider: ${i}.`);
            }
          }, sn = async (e) => {
            let t = I(), n = 0, o = [], r = e || {};
            $n(r);
            try {
              let i = Wn(r.graphOptimizationLevel ?? "all"), s = Gn(r.executionMode ?? "sequential"), a = typeof r.logId == "string" ? N(r.logId, o) : 0, u = r.logSeverityLevel ?? 2;
              if (!Number.isInteger(u) || u < 0 || u > 4) throw new Error(`log severity level is not valid: ${u}`);
              let f = r.logVerbosityLevel ?? 0;
              if (!Number.isInteger(f) || f < 0 || f > 4) throw new Error(`log verbosity level is not valid: ${f}`);
              let l = typeof r.optimizedModelFilePath == "string" ? N(r.optimizedModelFilePath, o) : 0;
              if (n = t._OrtCreateSessionOptions(i, !!r.enableCpuMemArena, !!r.enableMemPattern, s, !!r.enableProfiling, 0, a, u, f, l), n === 0 && S("Can't create session options."), r.executionProviders && await zn(n, r, o), r.enableGraphCapture !== void 0) {
                if (typeof r.enableGraphCapture != "boolean") throw new Error(`enableGraphCapture must be a boolean value: ${r.enableGraphCapture}`);
                ie(n, "enableGraphCapture", r.enableGraphCapture.toString(), o);
              }
              if (r.freeDimensionOverrides) for (let [c, d] of Object.entries(r.freeDimensionOverrides)) {
                if (typeof c != "string") throw new Error(`free dimension override name must be a string: ${c}`);
                if (typeof d != "number" || !Number.isInteger(d) || d < 0) throw new Error(`free dimension override value must be a non-negative integer: ${d}`);
                let p = N(c, o);
                t._OrtAddFreeDimensionOverride(n, p, d) !== 0 && S(`Can't set a free dimension override: ${c} - ${d}.`);
              }
              return r.extra !== void 0 && he(r.extra, "", /* @__PURE__ */ new WeakSet(), (c, d) => {
                ie(n, c, d, o);
              }), [n, o];
            } catch (i) {
              throw n !== 0 && t._OrtReleaseSessionOptions(n) !== 0 && S("Can't release session options."), o.forEach((s) => t._free(s)), i;
            }
          };
        });
        var ae, Fe, ue, un, fn, ke, We, cn, at = E(() => {
          "use strict";
          ae = (e) => {
            switch (e) {
              case "int8":
                return 3;
              case "uint8":
                return 2;
              case "bool":
                return 9;
              case "int16":
                return 5;
              case "uint16":
                return 4;
              case "int32":
                return 6;
              case "uint32":
                return 12;
              case "float16":
                return 10;
              case "float32":
                return 1;
              case "float64":
                return 11;
              case "string":
                return 8;
              case "int64":
                return 7;
              case "uint64":
                return 13;
              case "int4":
                return 22;
              case "uint4":
                return 21;
              default:
                throw new Error(`unsupported data type: ${e}`);
            }
          }, Fe = (e) => {
            switch (e) {
              case 3:
                return "int8";
              case 2:
                return "uint8";
              case 9:
                return "bool";
              case 5:
                return "int16";
              case 4:
                return "uint16";
              case 6:
                return "int32";
              case 12:
                return "uint32";
              case 10:
                return "float16";
              case 1:
                return "float32";
              case 11:
                return "float64";
              case 8:
                return "string";
              case 7:
                return "int64";
              case 13:
                return "uint64";
              case 22:
                return "int4";
              case 21:
                return "uint4";
              default:
                throw new Error(`unsupported data type: ${e}`);
            }
          }, ue = (e, t) => {
            let n = [-1, 4, 1, 1, 2, 2, 4, 8, -1, 1, 2, 8, 4, 8, -1, -1, -1, -1, -1, -1, -1, 0.5, 0.5][e], o = typeof t == "number" ? t : t.reduce((r, i) => r * i, 1);
            return n > 0 ? Math.ceil(o * n) : void 0;
          }, un = (e) => {
            switch (e) {
              case "float16":
                return typeof Float16Array < "u" ? Float16Array : Uint16Array;
              case "float32":
                return Float32Array;
              case "uint8":
                return Uint8Array;
              case "int8":
                return Int8Array;
              case "uint16":
                return Uint16Array;
              case "int16":
                return Int16Array;
              case "int32":
                return Int32Array;
              case "bool":
                return Uint8Array;
              case "float64":
                return Float64Array;
              case "uint32":
                return Uint32Array;
              case "int64":
                return BigInt64Array;
              case "uint64":
                return BigUint64Array;
              default:
                throw new Error(`unsupported type: ${e}`);
            }
          }, fn = (e) => {
            switch (e) {
              case "verbose":
                return 0;
              case "info":
                return 1;
              case "warning":
                return 2;
              case "error":
                return 3;
              case "fatal":
                return 4;
              default:
                throw new Error(`unsupported logging level: ${e}`);
            }
          }, ke = (e) => e === "float32" || e === "float16" || e === "int32" || e === "int64" || e === "uint32" || e === "uint8" || e === "bool" || e === "uint4" || e === "int4", We = (e) => e === "float32" || e === "float16" || e === "int32" || e === "int64" || e === "uint32" || e === "uint64" || e === "int8" || e === "uint8" || e === "bool" || e === "uint4" || e === "int4", cn = (e) => {
            switch (e) {
              case "none":
                return 0;
              case "cpu":
                return 1;
              case "cpu-pinned":
                return 2;
              case "texture":
                return 3;
              case "gpu-buffer":
                return 4;
              case "ml-tensor":
                return 5;
              default:
                throw new Error(`unsupported data location: ${e}`);
            }
          };
        });
        var be, ut = E(() => {
          "use strict";
          Be();
          be = async (e) => {
            if (typeof e == "string") if (false) try {
              let { readFile: t } = qe("node:fs/promises");
              return new Uint8Array(await t(e));
            } catch (t) {
              if (t.code === "ERR_FS_FILE_TOO_LARGE") {
                let { createReadStream: n } = qe("node:fs"), o = n(e), r = [];
                for await (let i of o) r.push(i);
                return new Uint8Array(Buffer.concat(r));
              }
              throw t;
            }
            else {
              let t = await fetch(e);
              if (!t.ok) throw new Error(`failed to load external data file: ${e}`);
              let n = t.headers.get("Content-Length"), o = n ? parseInt(n, 10) : 0;
              if (o < 1073741824) return new Uint8Array(await t.arrayBuffer());
              {
                if (!t.body) throw new Error(`failed to load external data file: ${e}, no response body.`);
                let r = t.body.getReader(), i;
                try {
                  i = new ArrayBuffer(o);
                } catch (a) {
                  if (a instanceof RangeError) {
                    let u = Math.ceil(o / 65536);
                    i = new WebAssembly.Memory({ initial: u, maximum: u }).buffer;
                  } else throw a;
                }
                let s = 0;
                for (; ; ) {
                  let { done: a, value: u } = await r.read();
                  if (a) break;
                  let f = u.byteLength;
                  new Uint8Array(i, s, f).set(u), s += f;
                }
                return new Uint8Array(i, 0, o);
              }
            }
            else return e instanceof Blob ? new Uint8Array(await e.arrayBuffer()) : e instanceof Uint8Array ? e : new Uint8Array(e);
          };
        });
        var Hn, Pe, _e, fe, jn, dn, we, De, Ue, ln, xe, ve, Ce, rt = E(() => {
          "use strict";
          X();
          on();
          an();
          at();
          ee();
          Ne();
          ut();
          Hn = (e, t) => {
            I()._OrtInit(e, t) !== 0 && S("Can't initialize onnxruntime.");
          }, Pe = async (e) => {
            Hn(e.wasm.numThreads, fn(e.logLevel));
          }, _e = async (e, t) => {
            I().asyncInit?.();
            let n = e.webgpu.adapter;
            if (t === "webgpu") {
              if (typeof navigator > "u" || !navigator.gpu) throw new Error("WebGPU is not supported in current environment");
              if (n) {
                if (typeof n.limits != "object" || typeof n.features != "object" || typeof n.requestDevice != "function") throw new Error("Invalid GPU adapter set in `env.webgpu.adapter`. It must be a GPUAdapter object.");
              } else {
                let o = e.webgpu.powerPreference;
                if (o !== void 0 && o !== "low-power" && o !== "high-performance") throw new Error(`Invalid powerPreference setting: "${o}"`);
                let r = e.webgpu.forceFallbackAdapter;
                if (r !== void 0 && typeof r != "boolean") throw new Error(`Invalid forceFallbackAdapter setting: "${r}"`);
                if (n = await navigator.gpu.requestAdapter({ powerPreference: o, forceFallbackAdapter: r }), !n) throw new Error('Failed to get GPU adapter. You may need to enable flag "--enable-unsafe-webgpu" if you are using Chrome.');
              }
            }
            if (t === "webnn" && (typeof navigator > "u" || !navigator.ml)) throw new Error("WebNN is not supported in current environment");
          }, fe = /* @__PURE__ */ new Map(), jn = (e) => {
            let t = I(), n = t.stackSave();
            try {
              let o = t.PTR_SIZE, r = t.stackAlloc(2 * o);
              t._OrtGetInputOutputCount(e, r, r + o) !== 0 && S("Can't get session input/output count.");
              let s = o === 4 ? "i32" : "i64";
              return [Number(t.getValue(r, s)), Number(t.getValue(r + o, s))];
            } finally {
              t.stackRestore(n);
            }
          }, dn = (e, t) => {
            let n = I(), o = n.stackSave(), r = 0;
            try {
              let i = n.PTR_SIZE, s = n.stackAlloc(2 * i);
              n._OrtGetInputOutputMetadata(e, t, s, s + i) !== 0 && S("Can't get session input/output metadata.");
              let u = Number(n.getValue(s, "*"));
              r = Number(n.getValue(s + i, "*"));
              let f = n.HEAP32[r / 4];
              if (f === 0) return [u, 0];
              let l = n.HEAPU32[r / 4 + 1], c = [];
              for (let d = 0; d < l; d++) {
                let p = Number(n.getValue(r + 8 + d * i, "*"));
                c.push(p !== 0 ? n.UTF8ToString(p) : Number(n.getValue(r + 8 + (d + l) * i, "*")));
              }
              return [u, f, c];
            } finally {
              n.stackRestore(o), r !== 0 && n._OrtFree(r);
            }
          }, we = (e) => {
            let t = I(), n = t._malloc(e.byteLength);
            if (n === 0) throw new Error(`Can't create a session. failed to allocate a buffer of size ${e.byteLength}.`);
            return t.HEAPU8.set(e, n), [n, e.byteLength];
          }, De = async (e, t) => {
            let n, o, r = I();
            Array.isArray(e) ? [n, o] = e : e.buffer === r.HEAPU8.buffer ? [n, o] = [e.byteOffset, e.byteLength] : [n, o] = we(e);
            let i = 0, s = 0, a = 0, u = [], f = [], l = [];
            try {
              if ([s, u] = await sn(t), t?.externalData && r.mountExternalData) {
                let g = [];
                for (let T of t.externalData) {
                  let U = typeof T == "string" ? T : T.path;
                  g.push(be(typeof T == "string" ? T : T.data).then((M) => {
                    r.mountExternalData(U, M);
                  }));
                }
                await Promise.all(g);
              }
              for (let g of t?.executionProviders ?? []) if ((typeof g == "string" ? g : g.name) === "webnn") {
                if (r.shouldTransferToMLTensor = false, typeof g != "string") {
                  let U = g, M = U?.context, v = U?.gpuDevice, le = U?.deviceType, re = U?.powerPreference;
                  M ? r.currentContext = M : v ? r.currentContext = await r.webnnCreateMLContext(v) : r.currentContext = await r.webnnCreateMLContext({ deviceType: le, powerPreference: re });
                } else r.currentContext = await r.webnnCreateMLContext();
                break;
              }
              i = await r._OrtCreateSession(n, o, s), r.webgpuOnCreateSession?.(i), i === 0 && S("Can't create a session."), r.jsepOnCreateSession?.(), r.currentContext && (r.webnnRegisterMLContext(i, r.currentContext), r.currentContext = void 0, r.shouldTransferToMLTensor = true);
              let [c, d] = jn(i), p = !!t?.enableGraphCapture, h = [], y = [], B = [], m = [], w = [];
              for (let g = 0; g < c; g++) {
                let [T, U, M] = dn(i, g);
                T === 0 && S("Can't get an input name."), f.push(T);
                let v = r.UTF8ToString(T);
                h.push(v), B.push(U === 0 ? { name: v, isTensor: false } : { name: v, isTensor: true, type: Fe(U), shape: M });
              }
              for (let g = 0; g < d; g++) {
                let [T, U, M] = dn(i, g + c);
                T === 0 && S("Can't get an output name."), l.push(T);
                let v = r.UTF8ToString(T);
                y.push(v), m.push(U === 0 ? { name: v, isTensor: false } : { name: v, isTensor: true, type: Fe(U), shape: M });
              }
              return fe.set(i, [i, f, l, null, p, false]), [i, h, y, B, m];
            } catch (c) {
              throw f.forEach((d) => r._OrtFree(d)), l.forEach((d) => r._OrtFree(d)), a !== 0 && r._OrtReleaseBinding(a) !== 0 && S("Can't release IO binding."), i !== 0 && r._OrtReleaseSession(i) !== 0 && S("Can't release session."), c;
            } finally {
              r._free(n), s !== 0 && r._OrtReleaseSessionOptions(s) !== 0 && S("Can't release session options."), u.forEach((c) => r._free(c)), r.unmountExternalData?.();
            }
          }, Ue = (e) => {
            let t = I(), n = fe.get(e);
            if (!n) throw new Error(`cannot release session. invalid session id: ${e}`);
            let [o, r, i, s, a] = n;
            s && (a && t._OrtClearBoundOutputs(s.handle) !== 0 && S("Can't clear bound outputs."), t._OrtReleaseBinding(s.handle) !== 0 && S("Can't release IO binding.")), t.jsepOnReleaseSession?.(e), t.webnnOnReleaseSession?.(e), t.webgpuOnReleaseSession?.(e), r.forEach((u) => t._OrtFree(u)), i.forEach((u) => t._OrtFree(u)), t._OrtReleaseSession(o) !== 0 && S("Can't release session."), fe.delete(e);
          }, ln = async (e, t, n, o, r, i, s = false) => {
            if (!e) {
              t.push(0);
              return;
            }
            let a = I(), u = a.PTR_SIZE, f = e[0], l = e[1], c = e[3], d = c, p, h;
            if (f === "string" && (c === "gpu-buffer" || c === "ml-tensor")) throw new Error("String tensor is not supported on GPU.");
            if (s && c !== "gpu-buffer") throw new Error(`External buffer must be provided for input/output index ${i} when enableGraphCapture is true.`);
            if (c === "gpu-buffer") {
              let m = e[2].gpuBuffer;
              h = ue(ae(f), l);
              {
                let w = a.jsepRegisterBuffer;
                if (!w) throw new Error('Tensor location "gpu-buffer" is not supported without using WebGPU.');
                p = w(o, i, m, h);
              }
            } else if (c === "ml-tensor") {
              let m = e[2].mlTensor;
              h = ue(ae(f), l);
              let w = a.webnnRegisterMLTensor;
              if (!w) throw new Error('Tensor location "ml-tensor" is not supported without using WebNN.');
              p = w(o, m, ae(f), l);
            } else {
              let m = e[2];
              if (Array.isArray(m)) {
                h = u * m.length, p = a._malloc(h), n.push(p);
                for (let w = 0; w < m.length; w++) {
                  if (typeof m[w] != "string") throw new TypeError(`tensor data at index ${w} is not a string`);
                  a.setValue(p + w * u, N(m[w], n), "*");
                }
              } else {
                let w = a.webnnIsGraphInput, O = a.webnnIsGraphOutput;
                if (f !== "string" && w && O) {
                  let g = a.UTF8ToString(r);
                  if (w(o, g) || O(o, g)) {
                    let T = ae(f);
                    h = ue(T, l), d = "ml-tensor";
                    let U = a.webnnCreateTemporaryTensor, M = a.webnnUploadTensor;
                    if (!U || !M) throw new Error('Tensor location "ml-tensor" is not supported without using WebNN.');
                    let v = await U(o, T, l);
                    M(v, new Uint8Array(m.buffer, m.byteOffset, m.byteLength)), p = v;
                  } else h = m.byteLength, p = a._malloc(h), n.push(p), a.HEAPU8.set(new Uint8Array(m.buffer, m.byteOffset, h), p);
                } else h = m.byteLength, p = a._malloc(h), n.push(p), a.HEAPU8.set(new Uint8Array(m.buffer, m.byteOffset, h), p);
              }
            }
            let y = a.stackSave(), B = a.stackAlloc(4 * l.length);
            try {
              l.forEach((w, O) => a.setValue(B + O * u, w, u === 4 ? "i32" : "i64"));
              let m = a._OrtCreateTensor(ae(f), p, h, B, l.length, cn(d));
              m === 0 && S(`Can't create tensor for input/output. session=${o}, index=${i}.`), t.push(m);
            } finally {
              a.stackRestore(y);
            }
          }, xe = async (e, t, n, o, r, i) => {
            let s = I(), a = s.PTR_SIZE, u = fe.get(e);
            if (!u) throw new Error(`cannot run inference. invalid session id: ${e}`);
            let f = u[0], l = u[1], c = u[2], d = u[3], p = u[4], h = u[5], y = t.length, B = o.length, m = 0, w = [], O = [], g = [], T = [], U = [], M = s.stackSave(), v = s.stackAlloc(y * a), le = s.stackAlloc(y * a), re = s.stackAlloc(B * a), lt = s.stackAlloc(B * a);
            try {
              [m, w] = rn(i), Y("wasm prepareInputOutputTensor");
              for (let b = 0; b < y; b++) await ln(n[b], O, T, e, l[t[b]], t[b], p);
              for (let b = 0; b < B; b++) await ln(r[b], g, T, e, c[o[b]], y + o[b], p);
              Z("wasm prepareInputOutputTensor");
              for (let b = 0; b < y; b++) s.setValue(v + b * a, O[b], "*"), s.setValue(le + b * a, l[t[b]], "*");
              for (let b = 0; b < B; b++) s.setValue(re + b * a, g[b], "*"), s.setValue(lt + b * a, c[o[b]], "*");
              s.jsepOnRunStart?.(f), s.webnnOnRunStart?.(f);
              let F;
              F = await s._OrtRun(f, le, v, y, lt, B, re, m), F !== 0 && S("failed to call OrtRun().");
              let z = [], pt = [];
              Y("wasm ProcessOutputTensor");
              for (let b = 0; b < B; b++) {
                let G = Number(s.getValue(re + b * a, "*"));
                if (G === g[b] || U.includes(g[b])) {
                  z.push(r[b]), G !== g[b] && s._OrtReleaseTensor(G) !== 0 && S("Can't release tensor.");
                  continue;
                }
                let mt = s.stackSave(), $ = s.stackAlloc(4 * a), oe = false, P, C = 0;
                try {
                  s._OrtGetTensorData(G, $, $ + a, $ + 2 * a, $ + 3 * a) !== 0 && S(`Can't access output tensor data on index ${b}.`);
                  let Ve = a === 4 ? "i32" : "i64", ge = Number(s.getValue($, Ve));
                  C = s.getValue($ + a, "*");
                  let wt = s.getValue($ + a * 2, "*"), Bn = Number(s.getValue($ + a * 3, Ve)), H = [];
                  for (let D = 0; D < Bn; D++) H.push(Number(s.getValue(wt + D * a, Ve)));
                  s._OrtFree(wt) !== 0 && S("Can't free memory for tensor dims.");
                  let j = H.reduce((D, L) => D * L, 1);
                  P = Fe(ge);
                  let pe = d?.outputPreferredLocations[o[b]];
                  if (P === "string") {
                    if (pe === "gpu-buffer" || pe === "ml-tensor") throw new Error("String tensor is not supported on GPU.");
                    let D = [];
                    for (let L = 0; L < j; L++) {
                      let V = s.getValue(C + L * a, "*"), Ee = s.getValue(C + (L + 1) * a, "*"), ht = L === j - 1 ? void 0 : Ee - V;
                      D.push(s.UTF8ToString(V, ht));
                    }
                    z.push([P, H, D, "cpu"]);
                  } else if (pe === "gpu-buffer" && j > 0) {
                    let D = s.jsepGetBuffer;
                    if (!D) throw new Error('preferredLocation "gpu-buffer" is not supported without using WebGPU.');
                    let L = D(C), V = ue(ge, j);
                    if (V === void 0 || !ke(P)) throw new Error(`Unsupported data type: ${P}`);
                    oe = true, z.push([P, H, { gpuBuffer: L, download: s.jsepCreateDownloader(L, V, P), dispose: () => {
                      s._OrtReleaseTensor(G) !== 0 && S("Can't release tensor.");
                    } }, "gpu-buffer"]);
                  } else if (pe === "ml-tensor" && j > 0) {
                    let D = s.webnnEnsureTensor, L = s.webnnIsGraphInputOutputTypeSupported;
                    if (!D || !L) throw new Error('preferredLocation "ml-tensor" is not supported without using WebNN.');
                    if (ue(ge, j) === void 0 || !We(P)) throw new Error(`Unsupported data type: ${P}`);
                    if (!L(e, P, false)) throw new Error(`preferredLocation "ml-tensor" for ${P} output is not supported by current WebNN Context.`);
                    let Ee = await D(e, C, ge, H, false);
                    oe = true, z.push([P, H, { mlTensor: Ee, download: s.webnnCreateMLTensorDownloader(C, P), dispose: () => {
                      s.webnnReleaseTensorId(C), s._OrtReleaseTensor(G);
                    } }, "ml-tensor"]);
                  } else if (pe === "ml-tensor-cpu-output" && j > 0) {
                    let D = s.webnnCreateMLTensorDownloader(C, P)(), L = z.length;
                    oe = true, pt.push((async () => {
                      let V = [L, await D];
                      return s.webnnReleaseTensorId(C), s._OrtReleaseTensor(G), V;
                    })()), z.push([P, H, [], "cpu"]);
                  } else {
                    let D = un(P), L = new D(j);
                    new Uint8Array(L.buffer, L.byteOffset, L.byteLength).set(s.HEAPU8.subarray(C, C + L.byteLength)), z.push([P, H, L, "cpu"]);
                  }
                } finally {
                  s.stackRestore(mt), P === "string" && C && s._free(C), oe || s._OrtReleaseTensor(G);
                }
              }
              d && !p && (s._OrtClearBoundOutputs(d.handle) !== 0 && S("Can't clear bound outputs."), fe.set(e, [f, l, c, d, p, false]));
              for (let [b, G] of await Promise.all(pt)) z[b][2] = G;
              return Z("wasm ProcessOutputTensor"), z;
            } finally {
              s.webnnOnRunEnd?.(f), s.stackRestore(M), O.forEach((F) => s._OrtReleaseTensor(F)), g.forEach((F) => s._OrtReleaseTensor(F)), T.forEach((F) => s._free(F)), m !== 0 && s._OrtReleaseRunOptions(m), w.forEach((F) => s._free(F));
            }
          }, ve = (e) => {
            let t = I(), n = fe.get(e);
            if (!n) throw new Error("invalid session id");
            let o = n[0], r = t._OrtEndProfiling(o);
            r === 0 && S("Can't get an profile file name."), t._OrtFree(r);
          }, Ce = (e) => {
            let t = [];
            for (let n of e) {
              let o = n[2];
              !Array.isArray(o) && "buffer" in o && t.push(o.buffer);
            }
            return t;
          };
        });
        var ne, W, ye, $e, ze, Ge, ft, ct, ce, de, Jn, pn, mn, wn, hn, bn, yn, gn, dt = E(() => {
          "use strict";
          X();
          rt();
          ee();
          Oe();
          ne = () => !!A.wasm.proxy && typeof document < "u", ye = false, $e = false, ze = false, ct = /* @__PURE__ */ new Map(), ce = (e, t) => {
            let n = ct.get(e);
            n ? n.push(t) : ct.set(e, [t]);
          }, de = () => {
            if (ye || !$e || ze || !W) throw new Error("worker not ready");
          }, Jn = (e) => {
            switch (e.data.type) {
              case "init-wasm":
                ye = false, e.data.err ? (ze = true, ft[1](e.data.err)) : ($e = true, ft[0]()), Ge && (URL.revokeObjectURL(Ge), Ge = void 0);
                break;
              case "init-ep":
              case "copy-from":
              case "create":
              case "release":
              case "run":
              case "end-profiling": {
                let t = ct.get(e.data.type);
                e.data.err ? t.shift()[1](e.data.err) : t.shift()[0](e.data.out);
                break;
              }
              default:
            }
          }, pn = async () => {
            if (!$e) {
              if (ye) throw new Error("multiple calls to 'initWasm()' detected.");
              if (ze) throw new Error("previous call to 'initWasm()' failed.");
              if (ye = true, ne()) return new Promise((e, t) => {
                W?.terminate(), en().then(([n, o]) => {
                  try {
                    W = o, W.onerror = (i) => t(i), W.onmessage = Jn, ft = [e, t];
                    let r = { type: "init-wasm", in: A };
                    if (!r.in.wasm.wasmPaths && n) {
                      let i = Me();
                      i && (r.in.wasm.wasmPaths = i);
                    }
                    W.postMessage(r), Ge = n;
                  } catch (r) {
                    t(r);
                  }
                }, t);
              });
              try {
                await Le(A.wasm), await Pe(A), $e = true;
              } catch (e) {
                throw ze = true, e;
              } finally {
                ye = false;
              }
            }
          }, mn = async (e) => {
            if (ne()) return de(), new Promise((t, n) => {
              ce("init-ep", [t, n]);
              let o = { type: "init-ep", in: { epName: e, env: A } };
              W.postMessage(o);
            });
            await _e(A, e);
          }, wn = async (e) => ne() ? (de(), new Promise((t, n) => {
            ce("copy-from", [t, n]);
            let o = { type: "copy-from", in: { buffer: e } };
            W.postMessage(o, [e.buffer]);
          })) : we(e), hn = async (e, t) => {
            if (ne()) {
              if (t?.preferredOutputLocation) throw new Error('session option "preferredOutputLocation" is not supported for proxy.');
              return de(), new Promise((n, o) => {
                ce("create", [n, o]);
                let r = { type: "create", in: { model: e, options: { ...t } } }, i = [];
                e instanceof Uint8Array && i.push(e.buffer), W.postMessage(r, i);
              });
            } else return De(e, t);
          }, bn = async (e) => {
            if (ne()) return de(), new Promise((t, n) => {
              ce("release", [t, n]);
              let o = { type: "release", in: e };
              W.postMessage(o);
            });
            Ue(e);
          }, yn = async (e, t, n, o, r, i) => {
            if (ne()) {
              if (n.some((s) => s[3] !== "cpu")) throw new Error("input tensor on GPU is not supported for proxy.");
              if (r.some((s) => s)) throw new Error("pre-allocated output tensor is not supported for proxy.");
              return de(), new Promise((s, a) => {
                ce("run", [s, a]);
                let u = n, f = { type: "run", in: { sessionId: e, inputIndices: t, inputs: u, outputIndices: o, options: i } };
                W.postMessage(f, Ce(u));
              });
            } else return xe(e, t, n, o, r, i);
          }, gn = async (e) => {
            if (ne()) return de(), new Promise((t, n) => {
              ce("end-profiling", [t, n]);
              let o = { type: "end-profiling", in: e };
              W.postMessage(o);
            });
            ve(e);
          };
        });
        var En, qn, He, Sn = E(() => {
          "use strict";
          X();
          dt();
          at();
          Be();
          ut();
          En = (e, t) => {
            switch (e.location) {
              case "cpu":
                return [e.type, e.dims, e.data, "cpu"];
              case "gpu-buffer":
                return [e.type, e.dims, { gpuBuffer: e.gpuBuffer }, "gpu-buffer"];
              case "ml-tensor":
                return [e.type, e.dims, { mlTensor: e.mlTensor }, "ml-tensor"];
              default:
                throw new Error(`invalid data location: ${e.location} for ${t()}`);
            }
          }, qn = (e) => {
            switch (e[3]) {
              case "cpu":
                return new k(e[0], e[2], e[1]);
              case "gpu-buffer": {
                let t = e[0];
                if (!ke(t)) throw new Error(`not supported data type: ${t} for deserializing GPU tensor`);
                let { gpuBuffer: n, download: o, dispose: r } = e[2];
                return k.fromGpuBuffer(n, { dataType: t, dims: e[1], download: o, dispose: r });
              }
              case "ml-tensor": {
                let t = e[0];
                if (!We(t)) throw new Error(`not supported data type: ${t} for deserializing MLTensor tensor`);
                let { mlTensor: n, download: o, dispose: r } = e[2];
                return k.fromMLTensor(n, { dataType: t, dims: e[1], download: o, dispose: r });
              }
              default:
                throw new Error(`invalid data location: ${e[3]}`);
            }
          }, He = class {
            async fetchModelAndCopyToWasmMemory(t) {
              return wn(await be(t));
            }
            async loadModel(t, n) {
              J();
              let o;
              typeof t == "string" ? o = await this.fetchModelAndCopyToWasmMemory(t) : o = t, [this.sessionId, this.inputNames, this.outputNames, this.inputMetadata, this.outputMetadata] = await hn(o, n), q();
            }
            async dispose() {
              return bn(this.sessionId);
            }
            async run(t, n, o) {
              J();
              let r = [], i = [];
              Object.entries(t).forEach((d) => {
                let p = d[0], h = d[1], y = this.inputNames.indexOf(p);
                if (y === -1) throw new Error(`invalid input '${p}'`);
                r.push(h), i.push(y);
              });
              let s = [], a = [];
              Object.entries(n).forEach((d) => {
                let p = d[0], h = d[1], y = this.outputNames.indexOf(p);
                if (y === -1) throw new Error(`invalid output '${p}'`);
                s.push(h), a.push(y);
              });
              let u = r.map((d, p) => En(d, () => `input "${this.inputNames[i[p]]}"`)), f = s.map((d, p) => d ? En(d, () => `output "${this.outputNames[a[p]]}"`) : null), l = await yn(this.sessionId, i, u, a, f, o), c = {};
              for (let d = 0; d < l.length; d++) c[this.outputNames[a[d]]] = s[d] ?? qn(l[d]);
              return q(), c;
            }
            startProfiling() {
            }
            endProfiling() {
              gn(this.sessionId);
            }
          };
        });
        var In = {};
        Se(In, { OnnxruntimeWebAssemblyBackend: () => je, initializeFlags: () => Tn, wasmBackend: () => Yn });
        var Tn, je, Yn, An = E(() => {
          "use strict";
          X();
          dt();
          Sn();
          Tn = () => {
            (typeof A.wasm.initTimeout != "number" || A.wasm.initTimeout < 0) && (A.wasm.initTimeout = 0);
            let e = A.wasm.simd;
            if (typeof e != "boolean" && e !== void 0 && e !== "fixed" && e !== "relaxed" && (console.warn(`Property "env.wasm.simd" is set to unknown value "${e}". Reset it to \`false\` and ignore SIMD feature checking.`), A.wasm.simd = false), typeof A.wasm.proxy != "boolean" && (A.wasm.proxy = false), typeof A.wasm.trace != "boolean" && (A.wasm.trace = false), typeof A.wasm.numThreads != "number" || !Number.isInteger(A.wasm.numThreads) || A.wasm.numThreads <= 0) if (typeof self < "u" && !self.crossOriginIsolated) A.wasm.numThreads = 1;
            else {
              let t = typeof navigator > "u" ? qe("node:os").cpus().length : navigator.hardwareConcurrency;
              A.wasm.numThreads = Math.min(4, Math.ceil((t || 1) / 2));
            }
          }, je = class {
            async init(t) {
              Tn(), await pn(), await mn(t);
            }
            async createInferenceSessionHandler(t, n) {
              let o = new He();
              return await o.loadModel(t, n), o;
            }
          }, Yn = new je();
        });
        var Xn = {};
        Se(Xn, { InferenceSession: () => Wt, TRACE: () => et, TRACE_EVENT_BEGIN: () => Y, TRACE_EVENT_END: () => Z, TRACE_FUNC_BEGIN: () => J, TRACE_FUNC_END: () => q, Tensor: () => k, default: () => Zn, env: () => A, registerBackend: () => se });
        X();
        X();
        X();
        var Vt = "1.27.0";
        var Zn = nt;
        {
          let e = (An(), Ye(In)).wasmBackend;
          se("cpu", e, 10), se("wasm", e, 10);
        }
        Object.defineProperty(A.versions, "web", { value: Vt, enumerable: true });
        return Ye(Xn);
      })();
      typeof exports == "object" && typeof module == "object" && (module.exports = ort2);
    }
  });

  // ../../tmp/ocr-runtime/src/direct-ocr-core.js
  var require_direct_ocr_core = __commonJS({
    "../../tmp/ocr-runtime/src/direct-ocr-core.js"(exports, module) {
      "use strict";
      function clamp(value, minimum, maximum) {
        return Math.max(minimum, Math.min(maximum, value));
      }
      function resizeWithin2(width, height, maximumEdge) {
        const longest = Math.max(width, height);
        if (longest <= maximumEdge) return { width, height };
        const scale = maximumEdge / longest;
        return {
          width: Math.max(1, Math.round(width * scale)),
          height: Math.max(1, Math.round(height * scale))
        };
      }
      function recognitionTargetWidth2(cropWidth, cropHeight, recognitionHeight = 48, baseWidth = 320, maximumWidth = 1280) {
        if (![cropWidth, cropHeight, recognitionHeight, baseWidth, maximumWidth].every(Number.isFinite) || cropWidth <= 0 || cropHeight <= 0 || recognitionHeight <= 0 || baseWidth <= 0 || maximumWidth < baseWidth) {
          throw new Error("invalid recognition dimensions");
        }
        const scaledWidth = Math.trunc(recognitionHeight * cropWidth / cropHeight);
        return Math.min(maximumWidth, Math.max(baseWidth, scaledWidth));
      }
      function rgbaToChw2(rgba, width, height, mean, standardDeviation) {
        const pixels = width * height;
        const output = new Float32Array(3 * pixels);
        for (let index = 0; index < pixels; index += 1) {
          const source = index * 4;
          output[index] = (rgba[source + 2] / 255 - mean[0]) / standardDeviation[0];
          output[index + pixels] = (rgba[source + 1] / 255 - mean[1]) / standardDeviation[1];
          output[index + pixels * 2] = (rgba[source] / 255 - mean[2]) / standardDeviation[2];
        }
        return output;
      }
      function foregroundColumnInk2(image) {
        const { data, width, height } = image;
        if (!data || !Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0 || data.length !== width * height * 4) throw new Error("invalid RGBA image data");
        const histogram = new Uint32Array(256);
        const gray = new Uint8Array(width * height);
        for (let index = 0; index < gray.length; index += 1) {
          const offset = index * 4;
          const value = Math.max(0, Math.min(255, Math.round(
            data[offset] * 0.299 + data[offset + 1] * 0.587 + data[offset + 2] * 0.114
          )));
          gray[index] = value;
          histogram[value] += 1;
        }
        const total = gray.length;
        let weightedTotal = 0;
        for (let level = 0; level < 256; level += 1) weightedTotal += level * histogram[level];
        let backgroundWeight = 0;
        let backgroundSum = 0;
        let bestVariance = -1;
        let threshold = 0;
        for (let level = 0; level < 256; level += 1) {
          backgroundWeight += histogram[level];
          if (!backgroundWeight) continue;
          const foregroundWeight = total - backgroundWeight;
          if (!foregroundWeight) break;
          backgroundSum += level * histogram[level];
          const meanBackground = backgroundSum / backgroundWeight;
          const meanForeground = (weightedTotal - backgroundSum) / foregroundWeight;
          const variance = backgroundWeight * foregroundWeight * (meanBackground - meanForeground) ** 2;
          if (variance > bestVariance) {
            bestVariance = variance;
            threshold = level;
          }
        }
        const mask = new Uint8Array(total);
        for (let index = 0; index < total; index += 1) mask[index] = gray[index] <= threshold ? 1 : 0;
        const eroded = new Uint8Array(total);
        for (let y = 0; y < height - 1; y += 1) {
          for (let x = 0; x < width - 1; x += 1) {
            const i = y * width + x;
            if (mask[i] && mask[i + 1] && mask[i + width] && mask[i + width + 1]) eroded[i] = 1;
          }
        }
        const opened = new Uint8Array(total);
        for (let y = 0; y < height; y += 1) {
          for (let x = 0; x < width; x += 1) {
            const i = y * width + x;
            if (!eroded[i]) continue;
            opened[i] = 1;
            if (x + 1 < width) opened[i + 1] = 1;
            if (y + 1 < height) opened[i + width] = 1;
            if (x + 1 < width && y + 1 < height) opened[i + width + 1] = 1;
          }
        }
        const columns = Array(width).fill(0);
        for (let y = 0; y < height; y += 1) {
          for (let x = 0; x < width; x += 1) columns[x] += opened[y * width + x];
        }
        return columns;
      }
      function splitHorizontalInkRanges2(columnInk, cropHeight) {
        if (!Number.isInteger(cropHeight) || cropHeight <= 0) throw new Error("cropHeight must be positive");
        const width = columnInk.length;
        if (width <= 1) return [[0, width]];
        const blankLimit = Math.max(1, Math.round(cropHeight * 0.02));
        const minimumGap = Math.max(1, Math.ceil(cropHeight * 0.5));
        const minimumContent = Math.max(1, Math.ceil(cropHeight * 0.25));
        const gaps = [];
        let start = null;
        for (let index = 0; index < width; index += 1) {
          const blank = columnInk[index] <= blankLimit;
          if (blank && start === null) start = index;
          else if (!blank && start !== null) {
            if (start > 0 && index < width && index - start >= minimumGap) gaps.push([start, index]);
            start = null;
          }
        }
        if (!gaps.length) return [[0, width]];
        const boundaries = [0, ...gaps.map(([left, right]) => Math.floor((left + right) / 2)), width];
        const ranges = [];
        for (let index = 0; index < boundaries.length - 1; index += 1) {
          const left = boundaries[index];
          const right = boundaries[index + 1];
          let first = -1;
          let last = -1;
          for (let x = left; x < right; x += 1) {
            if (columnInk[x] > blankLimit) {
              if (first < 0) first = x;
              last = x;
            }
          }
          if (first >= 0 && last - first + 1 >= minimumContent) ranges.push([left, right]);
        }
        return ranges.length ? ranges : [[0, width]];
      }
      function horizontalSubpolygon2(polygon, start, end, cropWidth) {
        if (!Array.isArray(polygon) || polygon.length !== 4 || cropWidth <= 0 || start < 0 || end <= start || end > cropWidth) throw new Error("invalid horizontal crop range");
        const [topLeft, topRight, bottomRight, bottomLeft] = polygon;
        const interpolate = (left, right, fraction) => [
          left[0] + (right[0] - left[0]) * fraction,
          left[1] + (right[1] - left[1]) * fraction
        ];
        const startFraction = start / cropWidth;
        const endFraction = end / cropWidth;
        return [
          interpolate(topLeft, topRight, startFraction),
          interpolate(topLeft, topRight, endFraction),
          interpolate(bottomLeft, bottomRight, endFraction),
          interpolate(bottomLeft, bottomRight, startFraction)
        ];
      }
      function cross(origin, a, b) {
        return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0]);
      }
      function convexHull(points) {
        const sorted = points.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
        if (sorted.length <= 2) return sorted;
        const lower = [];
        for (const point of sorted) {
          while (lower.length >= 2 && cross(lower.at(-2), lower.at(-1), point) <= 0) lower.pop();
          lower.push(point);
        }
        const upper = [];
        for (let index = sorted.length - 1; index >= 0; index -= 1) {
          const point = sorted[index];
          while (upper.length >= 2 && cross(upper.at(-2), upper.at(-1), point) <= 0) upper.pop();
          upper.push(point);
        }
        lower.pop();
        upper.pop();
        return lower.concat(upper);
      }
      function orderQuad(points) {
        const sums = points.map((point) => point[0] + point[1]);
        const differences = points.map((point) => point[0] - point[1]);
        const indices = [
          sums.indexOf(Math.min(...sums)),
          differences.indexOf(Math.max(...differences)),
          sums.indexOf(Math.max(...sums)),
          differences.indexOf(Math.min(...differences))
        ];
        if (new Set(indices).size === 4) return indices.map((index) => [...points[index]]);
        const ordered = points.slice().sort((a, b) => a[1] - b[1] || a[0] - b[0]);
        const top = ordered.slice(0, 2).sort((a, b) => a[0] - b[0]);
        const bottom = ordered.slice(2).sort((a, b) => a[0] - b[0]);
        return [[...top[0]], [...top[1]], [...bottom[1]], [...bottom[0]]];
      }
      function minimumAreaBox(points) {
        const hull = convexHull(points);
        if (hull.length < 2) return null;
        let best = null;
        for (let index = 0; index < hull.length; index += 1) {
          const current = hull[index];
          const next = hull[(index + 1) % hull.length];
          const angle = Math.atan2(next[1] - current[1], next[0] - current[0]);
          const cosine = Math.cos(angle);
          const sine = Math.sin(angle);
          let minX = Infinity;
          let minY = Infinity;
          let maxX = -Infinity;
          let maxY = -Infinity;
          for (const point of hull) {
            const x = point[0] * cosine + point[1] * sine;
            const y = -point[0] * sine + point[1] * cosine;
            minX = Math.min(minX, x);
            maxX = Math.max(maxX, x);
            minY = Math.min(minY, y);
            maxY = Math.max(maxY, y);
          }
          const area = (maxX - minX) * (maxY - minY);
          if (!best || area < best.area) best = { area, minX, minY, maxX, maxY, cosine, sine };
        }
        if (!best) return null;
        const rotated = [
          [best.minX, best.minY],
          [best.maxX, best.minY],
          [best.maxX, best.maxY],
          [best.minX, best.maxY]
        ];
        return orderQuad(rotated.map(([x, y]) => [
          x * best.cosine - y * best.sine,
          x * best.sine + y * best.cosine
        ]));
      }
      function distance2(a, b) {
        return Math.hypot(a[0] - b[0], a[1] - b[1]);
      }
      function orientationCandidates2(boxes) {
        let signed = 0;
        let magnitude = 0;
        let count = 0;
        for (const box of boxes) {
          const polygon = box?.poly;
          if (!Array.isArray(polygon) || polygon.length !== 4) {
            throw new Error("Orientation boxes must contain four-point polygons");
          }
          const width = Math.max(distance2(polygon[0], polygon[1]), distance2(polygon[2], polygon[3]), 1e-9);
          const height = Math.max(distance2(polygon[0], polygon[3]), distance2(polygon[1], polygon[2]), 1e-9);
          const aspect = Math.max(-3, Math.min(3, Math.log(width / height)));
          if (Math.abs(aspect) < Math.log(1.35)) continue;
          const score = box.score === void 0 ? 1 : Number(box.score);
          if (!Number.isFinite(score) || score < 0 || score > 1) {
            throw new Error("Orientation box scores must be between 0 and 1");
          }
          const weight = Math.max(0.05, score) * Math.min(Math.sqrt(width * height), 240);
          signed += weight * aspect;
          magnitude += weight * Math.abs(aspect);
          count += 1;
        }
        if (!count || magnitude <= 1e-9 || Math.abs(signed) / magnitude < 0.25) return [0, 90, 180, 270];
        return signed > 0 ? [0, 180] : [90, 270];
      }
      function selectOrientation2(scores, minimumMargin = 0.08) {
        const entries = Object.entries(scores).map(([rawRotation, rawScore]) => {
          const rotation = Number(rawRotation);
          const score = Number(rawScore);
          if (![0, 90, 180, 270].includes(rotation)) throw new Error("Unsupported orientation score rotation");
          if (!Number.isFinite(score) || score < 0 || score > 1) {
            throw new Error("Orientation probe scores must be between 0 and 1");
          }
          return [score, rotation];
        });
        if (!entries.length) return 0;
        entries.sort((a, b) => b[0] - a[0] || a[1] - b[1]);
        const [bestScore, bestRotation] = entries[0];
        const secondScore = entries.length > 1 ? entries[1][0] : 0;
        if (bestScore - secondScore < minimumMargin && Object.hasOwn(scores, "0")) return 0;
        return bestRotation;
      }
      function transformPolygonRightAngle(polygon, width, height, degrees) {
        if (!Array.isArray(polygon) || polygon.length !== 4) throw new Error("Polygon must contain four points");
        if (![0, 90, 180, 270].includes(degrees)) throw new Error("Unsupported right-angle rotation");
        const points = polygon.map(([x, y]) => {
          if (degrees === 0) return [x, y];
          if (degrees === 90) return [height - y, x];
          if (degrees === 180) return [width - x, height - y];
          return [y, width - x];
        });
        return orderQuad(points);
      }
      function canonicalizeBoxes2(boxes, width, height, degrees) {
        const transformed = boxes.map((box) => ({
          ...box,
          poly: transformPolygonRightAngle(box.poly, width, height, degrees)
        }));
        return {
          boxes: sortReadingOrder(transformed),
          width: degrees === 90 || degrees === 270 ? height : width,
          height: degrees === 90 || degrees === 270 ? width : height
        };
      }
      function orientationProbeIndices2(boxes, limit = 6) {
        if (!Number.isInteger(limit) || limit <= 0) throw new Error("Orientation probe limit must be positive");
        return boxes.map((box, index) => {
          const width = Math.max(distance2(box.poly[0], box.poly[1]), distance2(box.poly[2], box.poly[3]), 1e-9);
          const height = Math.max(distance2(box.poly[0], box.poly[3]), distance2(box.poly[1], box.poly[2]), 1e-9);
          const score = box.score === void 0 ? 1 : Number(box.score);
          const elongation = Math.max(width, height) / Math.min(width, height);
          return { index, quality: score * Math.min(elongation, 8) * Math.sqrt(width * height) };
        }).sort((a, b) => b.quality - a.quality || a.index - b.index).slice(0, limit).map((item) => item.index);
      }
      function minimumSide(box) {
        return Math.min(distance2(box[0], box[1]), distance2(box[1], box[2]));
      }
      function pointInside(pointX, pointY, polygon) {
        let inside = false;
        for (let current = 0, previous = polygon.length - 1; current < polygon.length; previous = current++) {
          const [currentX, currentY] = polygon[current];
          const [previousX, previousY] = polygon[previous];
          if (currentY > pointY !== previousY > pointY && pointX < (previousX - currentX) * (pointY - currentY) / (previousY - currentY || Number.EPSILON) + currentX) inside = !inside;
        }
        return inside;
      }
      function boxScore(probabilities, width, height, box) {
        const minX = clamp(Math.floor(Math.min(...box.map((point) => point[0]))), 0, width - 1);
        const maxX = clamp(Math.ceil(Math.max(...box.map((point) => point[0]))), 0, width - 1);
        const minY = clamp(Math.floor(Math.min(...box.map((point) => point[1]))), 0, height - 1);
        const maxY = clamp(Math.ceil(Math.max(...box.map((point) => point[1]))), 0, height - 1);
        let sum = 0;
        let count = 0;
        for (let y = minY; y <= maxY; y += 1) {
          for (let x = minX; x <= maxX; x += 1) {
            if (!pointInside(x + 0.5, y + 0.5, box)) continue;
            sum += probabilities[y * width + x];
            count += 1;
          }
        }
        return count ? sum / count : 0;
      }
      function expandBox(box, ratio) {
        const width = distance2(box[0], box[1]);
        const height = distance2(box[1], box[2]);
        if (width <= 0 || height <= 0) return null;
        const offset = width * height * ratio / (2 * (width + height));
        const center = box.reduce((sum, point) => [sum[0] + point[0] / 4, sum[1] + point[1] / 4], [0, 0]);
        const horizontal = [(box[1][0] - box[0][0]) / width, (box[1][1] - box[0][1]) / width];
        const vertical = [(box[3][0] - box[0][0]) / height, (box[3][1] - box[0][1]) / height];
        const halfWidth = width / 2 + offset;
        const halfHeight = height / 2 + offset;
        return [
          [-halfWidth, -halfHeight],
          [halfWidth, -halfHeight],
          [halfWidth, halfHeight],
          [-halfWidth, halfHeight]
        ].map(([x, y]) => [
          center[0] + horizontal[0] * x + vertical[0] * y,
          center[1] + horizontal[1] * x + vertical[1] * y
        ]);
      }
      function componentPoints(probabilities, width, height, threshold) {
        const visited = new Uint8Array(width * height);
        const queue = new Int32Array(width * height);
        const components = [];
        for (let start = 0; start < probabilities.length; start += 1) {
          if (visited[start] || probabilities[start] <= threshold) continue;
          let head = 0;
          let tail = 1;
          queue[0] = start;
          visited[start] = 1;
          const points = [];
          while (head < tail) {
            const offset = queue[head++];
            const x = offset % width;
            const y = Math.floor(offset / width);
            let boundary = false;
            for (let dy = -1; dy <= 1; dy += 1) {
              for (let dx = -1; dx <= 1; dx += 1) {
                if (dx === 0 && dy === 0) continue;
                const nextX = x + dx;
                const nextY = y + dy;
                if (nextX < 0 || nextY < 0 || nextX >= width || nextY >= height) {
                  boundary = true;
                  continue;
                }
                const next = nextY * width + nextX;
                if (probabilities[next] <= threshold) boundary = true;
                else if (!visited[next]) {
                  visited[next] = 1;
                  queue[tail++] = next;
                }
              }
            }
            if (boundary) points.push([x, y]);
          }
          if (points.length >= 4) components.push(points);
        }
        return components;
      }
      function sortReadingOrder(boxes) {
        const sorted = boxes.slice().sort((a, b) => a.poly[0][1] - b.poly[0][1] || a.poly[0][0] - b.poly[0][0]);
        for (let index = 0; index < sorted.length - 1; index += 1) {
          for (let cursor = index; cursor >= 0; cursor -= 1) {
            if (Math.abs(sorted[cursor + 1].poly[0][1] - sorted[cursor].poly[0][1]) < 10 && sorted[cursor + 1].poly[0][0] < sorted[cursor].poly[0][0]) {
              [sorted[cursor], sorted[cursor + 1]] = [sorted[cursor + 1], sorted[cursor]];
            } else break;
          }
        }
        return sorted;
      }
      function decodeDetectionMap2(probabilities, width, height, sourceWidth, sourceHeight, options) {
        const boxes = [];
        const components = componentPoints(probabilities, width, height, options.threshold).slice(0, 1e3);
        for (const points of components) {
          const box = minimumAreaBox(points);
          if (!box || minimumSide(box) < 3) continue;
          const score = boxScore(probabilities, width, height, box);
          if (score < options.boxThreshold) continue;
          const expanded = expandBox(box, options.unclipRatio);
          if (!expanded || minimumSide(expanded) < 5) continue;
          const poly = expanded.map((point) => [
            clamp(Math.round(point[0] * sourceWidth / width), 0, sourceWidth),
            clamp(Math.round(point[1] * sourceHeight / height), 0, sourceHeight)
          ]);
          boxes.push({ poly, score });
        }
        return sortReadingOrder(boxes);
      }
      function decodeCtc2(data, dimensions, dictionary, sampleIndex = 0) {
        const [, timeSteps, classes] = dimensions;
        const offset = sampleIndex * timeSteps * classes;
        let previous = -1;
        let text = "";
        const probabilities = [];
        for (let step = 0; step < timeSteps; step += 1) {
          let selected = 0;
          let maximum = -Infinity;
          for (let index = 0; index < classes; index += 1) {
            const value = data[offset + step * classes + index];
            if (value > maximum) {
              maximum = value;
              selected = index;
            }
          }
          if (selected > 0 && selected !== previous && dictionary[selected - 1] !== void 0) {
            text += dictionary[selected - 1];
            probabilities.push(maximum);
          }
          previous = selected;
        }
        return {
          text: text.normalize("NFC"),
          score: probabilities.length ? probabilities.reduce((sum, value) => sum + value, 0) / probabilities.length : 0
        };
      }
      module.exports = {
        canonicalizeBoxes: canonicalizeBoxes2,
        decodeCtc: decodeCtc2,
        decodeDetectionMap: decodeDetectionMap2,
        distance: distance2,
        foregroundColumnInk: foregroundColumnInk2,
        horizontalSubpolygon: horizontalSubpolygon2,
        orientationCandidates: orientationCandidates2,
        orientationProbeIndices: orientationProbeIndices2,
        recognitionTargetWidth: recognitionTargetWidth2,
        resizeWithin: resizeWithin2,
        rgbaToChw: rgbaToChw2,
        selectOrientation: selectOrientation2,
        splitHorizontalInkRanges: splitHorizontalInkRanges2,
        sortReadingOrder,
        transformPolygonRightAngle
      };
    }
  });

  // ../../tmp/ocr-runtime/src/direct-ocr-worker.js
  var ort = require_ort_wasm_min();
  var {
    canonicalizeBoxes,
    decodeCtc,
    decodeDetectionMap,
    distance,
    foregroundColumnInk,
    horizontalSubpolygon,
    orientationCandidates,
    orientationProbeIndices,
    recognitionTargetWidth,
    resizeWithin,
    rgbaToChw,
    selectOrientation,
    splitHorizontalInkRanges
  } = require_direct_ocr_core();
  var DETECTION_MODEL = "/ocr-assets/models/detection.onnx";
  var RECOGNITION_MODEL = "/ocr-assets/models/korean-recognition.onnx";
  var RECOGNITION_DICTIONARY = "/ocr-assets/models/korean-recognition-dictionary.json";
  var MAX_SOURCE_EDGE = 1280;
  var DETECTION_EDGE = 640;
  var RECOGNITION_HEIGHT = 48;
  var RECOGNITION_BASE_WIDTH = 320;
  var RECOGNITION_MAX_WIDTH = 1280;
  var RECOGNITION_BATCH_SIZE = 4;
  var DETECTION_NORMALIZE = {
    mean: [0.485, 0.456, 0.406],
    standardDeviation: [0.229, 0.224, 0.225]
  };
  var RECOGNITION_NORMALIZE = {
    mean: [0.5, 0.5, 0.5],
    standardDeviation: [0.5, 0.5, 0.5]
  };
  ort.env.wasm.wasmPaths = "/ocr-assets/ort/";
  ort.env.wasm.numThreads = 1;
  ort.env.wasm.proxy = false;
  var running = false;
  var detectionSession = null;
  var recognitionSession = null;
  function progress(value) {
    self.postMessage({ type: "progress", progress: value });
  }
  function createCanvas(width, height) {
    if (typeof OffscreenCanvas !== "function") {
      throw new Error("OffscreenCanvas is required for direct browser OCR");
    }
    return new OffscreenCanvas(width, height);
  }
  function context2d(canvas) {
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) throw new Error("Canvas 2D context is unavailable");
    return context;
  }
  function drawScaled(source, width, height) {
    const canvas = createCanvas(width, height);
    const context = context2d(canvas);
    context.fillStyle = "#fff";
    context.fillRect(0, 0, width, height);
    context.drawImage(source, 0, 0, width, height);
    return canvas;
  }
  function rotateCanvasRightAngle(source, degrees) {
    if (degrees === 0) return source;
    if (![90, 180, 270].includes(degrees)) throw new Error(`Unsupported page rotation: ${degrees}`);
    const swap = degrees === 90 || degrees === 270;
    const canvas = createCanvas(swap ? source.height : source.width, swap ? source.width : source.height);
    const context = context2d(canvas);
    if (degrees === 90) context.setTransform(0, 1, -1, 0, source.height, 0);
    else if (degrees === 180) context.setTransform(-1, 0, 0, -1, source.width, source.height);
    else context.setTransform(0, -1, 1, 0, 0, source.width);
    context.drawImage(source, 0, 0);
    context.resetTransform();
    return canvas;
  }
  function detectionDimensions(width, height) {
    const resized = resizeWithin(width, height, DETECTION_EDGE);
    return {
      width: Math.max(32, Math.round(resized.width / 32) * 32),
      height: Math.max(32, Math.round(resized.height / 32) * 32)
    };
  }
  function tensorFromCanvas(canvas, normalize) {
    const image = context2d(canvas).getImageData(0, 0, canvas.width, canvas.height);
    return new ort.Tensor(
      "float32",
      rgbaToChw(image.data, canvas.width, canvas.height, normalize.mean, normalize.standardDeviation),
      [1, 3, canvas.height, canvas.width]
    );
  }
  async function runSession(session, tensor) {
    const output = await session.run({ [session.inputNames[0]]: tensor });
    return output[session.outputNames[0]];
  }
  function cropRotated(source, polygon) {
    const width = Math.max(1, Math.floor(Math.max(
      distance(polygon[0], polygon[1]),
      distance(polygon[2], polygon[3])
    )));
    const height = Math.max(1, Math.floor(Math.max(
      distance(polygon[0], polygon[3]),
      distance(polygon[1], polygon[2])
    )));
    const angle = Math.atan2(
      polygon[1][1] - polygon[0][1],
      polygon[1][0] - polygon[0][0]
    );
    const cosine = Math.cos(angle);
    const sine = Math.sin(angle);
    const crop = createCanvas(width, height);
    const context = context2d(crop);
    context.setTransform(
      cosine,
      -sine,
      sine,
      cosine,
      -(cosine * polygon[0][0] + sine * polygon[0][1]),
      sine * polygon[0][0] - cosine * polygon[0][1]
    );
    context.drawImage(source, 0, 0);
    context.resetTransform();
    if (height / width < 1.5) return crop;
    const rotated = createCanvas(height, width);
    const rotatedContext = context2d(rotated);
    rotatedContext.setTransform(0, -1, 1, 0, 0, width);
    rotatedContext.drawImage(crop, 0, 0);
    rotatedContext.resetTransform();
    crop.width = 1;
    crop.height = 1;
    return rotated;
  }
  function isTallPolygon(polygon) {
    const width = Math.max(distance(polygon[0], polygon[1]), distance(polygon[2], polygon[3]));
    const height = Math.max(distance(polygon[0], polygon[3]), distance(polygon[1], polygon[2]));
    return height / Math.max(width, 1e-9) >= 1.5;
  }
  function sliceCrop(crop, start, end) {
    const width = end - start;
    const result = createCanvas(width, crop.height);
    context2d(result).drawImage(crop, start, 0, width, crop.height, 0, 0, width, crop.height);
    return result;
  }
  function refineDetectedBoxes(sourceCanvas, boxes) {
    const refined = [];
    for (const box of boxes) {
      const crop = cropRotated(sourceCanvas, box.poly);
      let ranges = [[0, crop.width]];
      if (!isTallPolygon(box.poly) && crop.width > 1) {
        const image = context2d(crop).getImageData(0, 0, crop.width, crop.height);
        ranges = splitHorizontalInkRanges(foregroundColumnInk(image), crop.height);
      }
      for (const [start, end] of ranges) {
        const poly = start === 0 && end === crop.width ? box.poly : horizontalSubpolygon(box.poly, start, end, crop.width);
        refined.push({ poly, score: box.score, crop: sliceCrop(crop, start, end) });
      }
      crop.width = 1;
      crop.height = 1;
    }
    return refined;
  }
  function prepareRecognitionSample(crop, inputIndex, width = recognitionTargetWidth(
    crop.width,
    crop.height,
    RECOGNITION_HEIGHT,
    RECOGNITION_BASE_WIDTH,
    RECOGNITION_MAX_WIDTH
  )) {
    const ratio = crop.width / Math.max(1, crop.height);
    const resizedWidth = Math.min(width, Math.ceil(RECOGNITION_HEIGHT * ratio));
    const canvas = createCanvas(width, RECOGNITION_HEIGHT);
    const context = context2d(canvas);
    context.fillStyle = "rgb(128, 128, 128)";
    context.fillRect(0, 0, width, RECOGNITION_HEIGHT);
    context.drawImage(crop, 0, 0, resizedWidth, RECOGNITION_HEIGHT);
    const image = context.getImageData(0, 0, width, RECOGNITION_HEIGHT);
    const chw = rgbaToChw(
      image.data,
      width,
      RECOGNITION_HEIGHT,
      RECOGNITION_NORMALIZE.mean,
      RECOGNITION_NORMALIZE.standardDeviation
    );
    canvas.width = 1;
    canvas.height = 1;
    crop.width = 1;
    crop.height = 1;
    return { inputIndex, width, chw };
  }
  function recognitionBatchTensor(samples) {
    const maxWidth = Math.max(...samples.map((sample) => sample.width));
    const plane = RECOGNITION_HEIGHT * maxWidth;
    const output = new Float32Array(samples.length * 3 * plane);
    for (let batch = 0; batch < samples.length; batch += 1) {
      const sample = samples[batch];
      const sourcePlane = RECOGNITION_HEIGHT * sample.width;
      for (let channel = 0; channel < 3; channel += 1) {
        for (let row = 0; row < RECOGNITION_HEIGHT; row += 1) {
          const source = channel * sourcePlane + row * sample.width;
          const target = batch * 3 * plane + channel * plane + row * maxWidth;
          output.set(sample.chw.subarray(source, source + sample.width), target);
        }
      }
    }
    return new ort.Tensor("float32", output, [samples.length, 3, RECOGNITION_HEIGHT, maxWidth]);
  }
  async function initializeDetection() {
    const [dictionaryResponse, det] = await Promise.all([
      fetch(RECOGNITION_DICTIONARY),
      ort.InferenceSession.create(DETECTION_MODEL, {
        executionProviders: ["wasm"],
        graphOptimizationLevel: "all"
      })
    ]);
    if (!dictionaryResponse.ok) throw new Error(`Dictionary HTTP ${dictionaryResponse.status}`);
    detectionSession = det;
    return dictionaryResponse.json();
  }
  async function initializeRecognition() {
    recognitionSession = await ort.InferenceSession.create(RECOGNITION_MODEL, {
      executionProviders: ["wasm"],
      graphOptimizationLevel: "all"
    });
  }
  async function detect(sourceCanvas) {
    const dimensions = detectionDimensions(sourceCanvas.width, sourceCanvas.height);
    const canvas = drawScaled(sourceCanvas, dimensions.width, dimensions.height);
    const output = await runSession(
      detectionSession,
      tensorFromCanvas(canvas, DETECTION_NORMALIZE)
    );
    canvas.width = 1;
    canvas.height = 1;
    const dims = output.dims;
    if (dims.length !== 3 && dims.length !== 4) {
      throw new Error(`Unexpected detection output: [${dims.join(",")}]`);
    }
    const height = dims.length === 4 ? dims[2] : dims[1];
    const width = dims.length === 4 ? dims[3] : dims[2];
    return decodeDetectionMap(
      output.data,
      width,
      height,
      sourceCanvas.width,
      sourceCanvas.height,
      { threshold: 0.3, boxThreshold: 0.6, unclipRatio: 1.5 }
    );
  }
  async function recognizeBoxes(refinedBoxes, dictionary, { reportProgress = true } = {}) {
    const ordered = refinedBoxes.map((box, inputIndex) => ({
      inputIndex,
      width: recognitionTargetWidth(
        box.crop.width,
        box.crop.height,
        RECOGNITION_HEIGHT,
        RECOGNITION_BASE_WIDTH,
        RECOGNITION_MAX_WIDTH
      )
    })).sort((a, b) => a.width - b.width);
    const decoded = [];
    for (let start = 0; start < ordered.length; start += RECOGNITION_BATCH_SIZE) {
      const batch = ordered.slice(start, start + RECOGNITION_BATCH_SIZE).map((work) => prepareRecognitionSample(refinedBoxes[work.inputIndex].crop, work.inputIndex, work.width));
      const output = await runSession(recognitionSession, recognitionBatchTensor(batch));
      for (let index = 0; index < batch.length; index += 1) {
        decoded.push({ inputIndex: batch[index].inputIndex, ...decodeCtc(
          output.data,
          output.dims,
          dictionary,
          index
        ) });
      }
      if (reportProgress) {
        progress(60 + Math.round(30 * Math.min(ordered.length, start + batch.length) / Math.max(1, ordered.length)));
      }
    }
    decoded.sort((a, b) => a.inputIndex - b.inputIndex);
    return decoded.map(({ inputIndex, ...result }) => ({
      ...result,
      poly: refinedBoxes[inputIndex].poly
    })).filter((item) => item.text && item.score >= 0);
  }
  function orientationProbeQuality(decoded, expectedCount) {
    if (!expectedCount) return 0;
    let total = 0;
    for (const item of decoded) {
      const compact = String(item.text || "").replace(/\s+/g, "");
      total += Number(item.score || 0) * Math.min(compact.length / 3, 1);
    }
    return Math.max(0, Math.min(1, total / expectedCount));
  }
  async function canonicalizePageOrientation(sourceCanvas, boxes, dictionary) {
    const candidates = orientationCandidates(boxes);
    if (!boxes.length) {
      return {
        canvas: sourceCanvas,
        boxes,
        orientation: {
          method: "detector_axis_recognizer_probe_v1",
          candidates,
          probe_scores: {},
          probe_regions: 0,
          applied_rotation_degrees: 0
        }
      };
    }
    const selected = orientationProbeIndices(boxes, 6).map((index) => boxes[index]);
    const scores = {};
    let probeRegions = 0;
    for (const rotation2 of candidates) {
      const rotated = rotateCanvasRightAngle(sourceCanvas, rotation2);
      const canonical2 = canonicalizeBoxes(selected, sourceCanvas.width, sourceCanvas.height, rotation2);
      const refined = refineDetectedBoxes(rotated, canonical2.boxes);
      const decoded = await recognizeBoxes(refined, dictionary, { reportProgress: false });
      scores[rotation2] = orientationProbeQuality(decoded, refined.length);
      probeRegions += refined.length;
      if (rotated !== sourceCanvas) {
        rotated.width = 1;
        rotated.height = 1;
      }
    }
    const rotation = selectOrientation(scores);
    const canonical = canonicalizeBoxes(boxes, sourceCanvas.width, sourceCanvas.height, rotation);
    const canvas = rotateCanvasRightAngle(sourceCanvas, rotation);
    return {
      canvas,
      boxes: canonical.boxes,
      orientation: {
        method: "detector_axis_recognizer_probe_v1",
        candidates,
        probe_scores: Object.fromEntries(candidates.map((candidate) => [candidate, Number(scores[candidate].toFixed(6))])),
        probe_regions: probeRegions,
        applied_rotation_degrees: rotation
      }
    };
  }
  async function dispose() {
    const sessions = [detectionSession, recognitionSession];
    detectionSession = null;
    recognitionSession = null;
    await Promise.all(sessions.map((session) => session?.release()));
  }
  async function recognize(image) {
    progress(5);
    const bitmap = await createImageBitmap(image);
    const sourceDimensions = resizeWithin(bitmap.width, bitmap.height, MAX_SOURCE_EDGE);
    let sourceCanvas = drawScaled(bitmap, sourceDimensions.width, sourceDimensions.height);
    bitmap.close();
    progress(10);
    const dictionary = await initializeDetection();
    progress(35);
    let boxes = await detect(sourceCanvas);
    await detectionSession.release();
    detectionSession = null;
    await initializeRecognition();
    const canonical = await canonicalizePageOrientation(sourceCanvas, boxes, dictionary);
    if (canonical.canvas !== sourceCanvas) {
      sourceCanvas.width = 1;
      sourceCanvas.height = 1;
      sourceCanvas = canonical.canvas;
    }
    boxes = canonical.boxes;
    const refinedBoxes = refineDetectedBoxes(sourceCanvas, boxes);
    progress(60);
    const items = (await recognizeBoxes(refinedBoxes, dictionary)).map((item, index) => ({
      ...item,
      id: `region-${String(index + 1).padStart(4, "0")}`
    }));
    await recognitionSession.release();
    recognitionSession = null;
    sourceCanvas.width = 1;
    sourceCanvas.height = 1;
    await dispose();
    progress(100);
    self.postMessage({
      type: "result",
      region_count: items.length,
      orientation: canonical.orientation,
      items
    });
  }
  self.onmessage = (event) => {
    if (event.data?.type !== "recognize") return;
    if (running) {
      self.postMessage({ type: "error", error: "OCR worker is already running" });
      return;
    }
    running = true;
    void recognize(event.data.image).catch(async (error) => {
      await dispose().catch(() => null);
      self.postMessage({
        type: "error",
        error: error instanceof Error ? error.message : "Direct ONNX OCR failed"
      });
    });
  };
})();
/*! Bundled license information:

onnxruntime-web/dist/ort.wasm.min.js:
  (*!
   * ONNX Runtime Web v1.27.0
   * Copyright (c) Microsoft Corporation. All rights reserved.
   * Licensed under the MIT License.
   *)
*/
