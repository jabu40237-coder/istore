/* i Store 3D hero — floating glass orbs + particles, GPU-friendly.
   Falls back to a static gradient if WebGL/Three.js unavailable or
   prefers-reduced-motion is set. */
(function () {
  var canvas = document.getElementById("hero3d");
  if (!canvas) return;

  function fallback() {
    canvas.style.background =
      "radial-gradient(ellipse 60% 50% at 50% 40%, rgba(108,92,231,.28), transparent 70%)," +
      "radial-gradient(ellipse 40% 35% at 70% 70%, rgba(0,214,143,.10), transparent 70%)," +
      "#0a0b10";
  }

  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { fallback(); return; }
  if (typeof THREE === "undefined") { fallback(); return; }
  try {
    var renderer = new THREE.WebGLRenderer({ canvas: canvas, alpha: true, antialias: true });
  } catch (e) { fallback(); return; }

  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  var scene = new THREE.Scene();
  var camera = new THREE.PerspectiveCamera(60, 1, 0.1, 100);
  camera.position.z = 9;

  // glowing orbs (abstract digital infrastructure)
  var orbs = [];
  var colors = [0x6c5ce7, 0x00d68f, 0x8e7bff];
  for (var i = 0; i < 7; i++) {
    var geo = new THREE.SphereGeometry(0.5 + Math.random() * 0.9, 32, 32);
    var mat = new THREE.MeshStandardMaterial({
      color: colors[i % colors.length], transparent: true, opacity: 0.55,
      roughness: 0.25, metalness: 0.6, emissive: colors[i % colors.length], emissiveIntensity: 0.35
    });
    var m = new THREE.Mesh(geo, mat);
    m.position.set((Math.random() - 0.5) * 14, (Math.random() - 0.5) * 8, (Math.random() - 0.5) * 4 - 1);
    m.userData = { s: 0.3 + Math.random() * 0.7, o: Math.random() * Math.PI * 2,
                   x: m.position.x, y: m.position.y };
    scene.add(m); orbs.push(m);
  }

  // particles
  var pGeo = new THREE.BufferGeometry();
  var pCount = 220, pos = new Float32Array(pCount * 3);
  for (var j = 0; j < pCount; j++) {
    pos[j * 3] = (Math.random() - 0.5) * 18;
    pos[j * 3 + 1] = (Math.random() - 0.5) * 10;
    pos[j * 3 + 2] = (Math.random() - 0.5) * 6;
  }
  pGeo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  var particles = new THREE.Points(pGeo, new THREE.PointsMaterial({
    color: 0x9d94ff, size: 0.045, transparent: true, opacity: 0.7 }));
  scene.add(particles);

  scene.add(new THREE.AmbientLight(0xffffff, 0.7));
  var dl = new THREE.DirectionalLight(0xffffff, 0.9);
  dl.position.set(5, 8, 6); scene.add(dl);

  var mx = 0, my = 0;  // subtle parallax
  window.addEventListener("pointermove", function (e) {
    mx = (e.clientX / window.innerWidth - 0.5);
    my = (e.clientY / window.innerHeight - 0.5);
  });

  function resize() {
    var w = canvas.clientWidth || window.innerWidth,
        h = canvas.clientHeight || window.innerHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.updateProjectionMatrix();
  }
  window.addEventListener("resize", resize); resize();

  var clock = new THREE.Clock();
  var running = true;
  document.addEventListener("visibilitychange", function () {
    running = !document.hidden;
    if (running) { clock.getDelta(); requestAnimationFrame(tick); }
  });

  function tick() {
    if (!running) return;
    var t = clock.getElapsedTime();
    for (var i = 0; i < orbs.length; i++) {
      var o = orbs[i], u = o.userData;
      o.position.y = u.y + Math.sin(t * u.s + u.o) * 0.7;
      o.position.x = u.x + Math.cos(t * u.s * 0.6 + u.o) * 0.4;
      o.rotation.x = t * 0.15 * u.s; o.rotation.y = t * 0.2 * u.s;
    }
    particles.rotation.y = t * 0.02;
    camera.position.x += (mx * 1.6 - camera.position.x) * 0.04;
    camera.position.y += (-my * 1.0 - camera.position.y) * 0.04;
    camera.lookAt(0, 0, 0);
    renderer.render(scene, camera);
    requestAnimationFrame(tick);
  }
  tick();
})();
