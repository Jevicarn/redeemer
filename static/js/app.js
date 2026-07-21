(() => {
  const role = document.body?.dataset?.role || "";
  const clock = document.getElementById("liveClock");
  const selectedStudentId = window.JEVICARN?.selectedStudentId ?? null;
  const installBtn = document.getElementById("installBtn");
  let deferredPrompt = null;

  function pad(n) { return String(n).padStart(2, "0"); }
  function renderClock() {
    if (!clock) return;
    const d = new Date();
    clock.textContent = d.toLocaleDateString(undefined, { weekday: "short", year: "numeric", month: "short", day: "numeric" }) +
      " • " + pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }
  renderClock();
  setInterval(renderClock, 1000);

  // Active nav state
  const navLinks = [...document.querySelectorAll(".nav-link")];
  const sections = navLinks
    .map(a => document.querySelector(a.getAttribute("href")))
    .filter(Boolean);

  const setActiveNav = () => {
    let active = null;
    const scrollY = window.scrollY + 120;
    for (const section of sections) {
      if (section.offsetTop <= scrollY) active = section.id;
    }
    navLinks.forEach(a => {
      a.classList.toggle("active", a.getAttribute("href") === "#" + (active || "overview"));
    });
  };
  window.addEventListener("scroll", setActiveNav, { passive: true });
  setActiveNav();

  // PWA install prompt
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredPrompt = e;
    if (installBtn) installBtn.hidden = false;
  });
  installBtn?.addEventListener("click", async () => {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    await deferredPrompt.userChoice;
    deferredPrompt = null;
    installBtn.hidden = true;
  });

  // Service worker
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", async () => {
      try {
        await navigator.serviceWorker.register("/sw.js");
      } catch (err) {
        console.warn("SW registration failed", err);
      }
    });
  }

  // Student modal
  const modal = document.getElementById("studentModal");
  const closeBtn = document.getElementById("closeStudentModal");
  const openBtn = document.getElementById("openStudentBtn");
  const modalEls = {
    name: document.getElementById("modalStudentName"),
    admission: document.getElementById("modalAdmission"),
    grade: document.getElementById("modalGrade"),
    status: document.getElementById("modalStatus"),
    balance: document.getElementById("modalBalance"),
    paymentsBody: document.getElementById("modalPaymentsBody"),
  };

  function renderPaymentRows(payments = []) {
    if (!modalEls.paymentsBody) return;
    modalEls.paymentsBody.innerHTML = payments.length
      ? payments.map(p => `
        <tr>
          <td>${(p.created_at || "").slice(0, 16)}</td>
          <td>KES ${Number(p.amount || 0).toLocaleString()}</td>
          <td>${p.method || ""}</td>
          <td>${p.recorded_by_name || ""}</td>
          <td><span class="pill ${String(p.status || "").toLowerCase()}">${p.status || ""}</span></td>
        </tr>
      `).join("")
      : `<tr><td colspan="5" class="muted">No payments found.</td></tr>`;
  }

  async function loadStudent(studentId) {
    if (!studentId) return;
    try {
      const res = await fetch(`/api/student/${studentId}`);
      if (!res.ok) throw new Error("Failed to load student");
      const data = await res.json();
      const s = data.student;
      modalEls.name.textContent = s.full_name;
      modalEls.admission.textContent = s.admission_no;
      modalEls.grade.textContent = s.grade;
      modalEls.status.textContent = s.payment_status;
      modalEls.balance.textContent = "KES " + Number(s.balance || 0).toLocaleString();
      renderPaymentRows(data.payments || []);
      modal?.showModal();
    } catch (err) {
      console.warn(err);
    }
  }

  document.querySelectorAll(".js-view-student").forEach(btn => {
    btn.addEventListener("click", () => loadStudent(btn.dataset.studentId));
  });
  openBtn?.addEventListener("click", () => {
    if (selectedStudentId) loadStudent(selectedStudentId);
  });
  closeBtn?.addEventListener("click", () => modal?.close());
  modal?.addEventListener("click", (e) => {
    const rect = modal.querySelector(".modal-card")?.getBoundingClientRect();
    if (!rect) return;
    const inDialog = (
      e.clientX >= rect.left &&
      e.clientX <= rect.right &&
      e.clientY >= rect.top &&
      e.clientY <= rect.bottom
    );
    if (!inDialog) modal.close();
  });

  // Make table rows selectable
  document.querySelectorAll(".student-row").forEach(row => {
    row.addEventListener("click", (e) => {
      if (e.target.closest("button, form, a")) return;
      loadStudent(row.dataset.studentId);
    });
  });

  // Keep forms resilient
  document.querySelectorAll("form").forEach(form => {
    form.addEventListener("submit", () => {
      const btn = form.querySelector("button[type='submit']");
      if (btn) {
        btn.disabled = true;
        setTimeout(() => btn.disabled = false, 3000);
      }
    });
  });
})();
