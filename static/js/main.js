/* i Store — global UI helpers */
(function () {
  // mobile nav
  var toggle = document.querySelector(".nav-toggle");
  var nav = document.querySelector(".nav");
  if (toggle && nav) toggle.addEventListener("click", function () {
    nav.classList.toggle("mobile-open");
  });

  // live price on order form
  var qtyInput = document.getElementById("quantity");
  var priceBox = document.getElementById("live-price");
  if (qtyInput && priceBox) {
    var sid = qtyInput.getAttribute("data-service");
    var per1000 = parseFloat(qtyInput.getAttribute("data-per1000") || "0");
    qtyInput.addEventListener("input", function () {
      var q = parseInt(qtyInput.value, 10) || 0;
      var total = per1000 * q / 1000;
      priceBox.textContent = "$" + total.toFixed(4);
    });
  }
})();
