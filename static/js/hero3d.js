/* i Store — Live interactive 3D hero background (Three.js)
   Light theme: floating glossy orange/white shapes, mouse & touch reactive. */
(function () {
  var canvas = document.getElementById('hero3d-live');
  if (!canvas || !window.THREE) return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  var renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas: canvas, alpha: true, antialias: true });
  } catch (e) { return; }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

  var scene = new THREE.Scene();
  var camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  camera.position.set(0, 0, 14);

  // soft studio lighting
  scene.add(new THREE.AmbientLight(0xffffff, 0.85));
  var key = new THREE.DirectionalLight(0xffffff, 1.1);
  key.position.set(6, 8, 10);
  scene.add(key);
  var fill = new THREE.DirectionalLight(0xffd9b3, 0.5);
  fill.position.set(-6, -4, 6);
  scene.add(fill);

  var COLORS = [0xF97316, 0xFB923C, 0xFDBA74, 0xFFFFFF, 0xEA580C];
  var shapes = [];
  var group = new THREE.Group();
  scene.add(group);

  function mat(color) {
    return new THREE.MeshStandardMaterial({
      color: color, roughness: 0.32, metalness: 0.25
    });
  }

  function addMesh(geo, i) {
    var m = new THREE.Mesh(geo, mat(COLORS[i % COLORS.length]));
    var spread = 9;
    m.position.set((Math.random() - 0.5) * spread * 1.6, (Math.random() - 0.5) * spread, (Math.random() - 0.5) * 5 - 1);
    m.rotation.set(Math.random() * Math.PI, Math.random() * Math.PI, 0);
    m.userData = {
      rx: (Math.random() - 0.5) * 0.012,
      ry: (Math.random() - 0.5) * 0.012,
      fy: Math.random() * Math.PI * 2,
      fs: 0.4 + Math.random() * 0.8,
      amp: 0.25 + Math.random() * 0.45,
      baseY: m.position.y
    };
    var s = 0.45 + Math.random() * 0.85;
    m.scale.set(s, s, s);
    group.add(m);
    shapes.push(m);
  }

  var geos = [
    new THREE.SphereGeometry(0.55, 32, 32),
    new THREE.TorusGeometry(0.5, 0.2, 20, 40),
    new THREE.CylinderGeometry(0.5, 0.5, 0.18, 32),
    new THREE.IcosahedronGeometry(0.55, 0),
    new THREE.BoxGeometry(0.8, 0.8, 0.8),
    new THREE.TorusKnotGeometry(0.4, 0.14, 80, 16)
  ];
  for (var i = 0; i < 26; i++) addMesh(geos[i % geos.length], i);

  // pointer parallax (mouse + touch)
  var tx = 0, ty = 0, cx = 0, cy = 0;
  function onPointer(x, y) {
    tx = (x / window.innerWidth - 0.5) * 2;
    ty = (y / window.innerHeight - 0.5) * 2;
  }
  window.addEventListener('pointermove', function (e) { onPointer(e.clientX, e.clientY); }, { passive: true });

  function resize() {
    var w = canvas.clientWidth || canvas.parentElement.clientWidth || 1;
    var h = canvas.clientHeight || canvas.parentElement.clientHeight || 1;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  window.addEventListener('resize', resize);
  resize();

  var clock = new THREE.Clock();
  var running = true;
  document.addEventListener('visibilitychange', function () {
    running = !document.hidden;
    if (running) { clock.getDelta(); loop(); }
  });

  function loop() {
    if (!running) return;
    requestAnimationFrame(loop);
    var t = clock.getElapsedTime();

    cx += (tx - cx) * 0.04;
    cy += (ty - cy) * 0.04;
    camera.position.x = cx * 2.2;
    camera.position.y = -cy * 1.4;
    camera.lookAt(0, 0, 0);
    group.rotation.y = cx * 0.25;
    group.rotation.x = cy * 0.15;

    for (var i = 0; i < shapes.length; i++) {
      var m = shapes[i], u = m.userData;
      m.rotation.x += u.rx;
      m.rotation.y += u.ry;
      m.position.y = u.baseY + Math.sin(t * u.fs + u.fy) * u.amp;
    }
    renderer.render(scene, camera);
  }
  loop();
})();
