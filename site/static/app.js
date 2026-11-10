(function () {
  document.querySelectorAll('form[data-confirma]').forEach(function (f) {
    f.addEventListener('submit', function (e) {
      if (!window.confirm(f.dataset.confirma)) e.preventDefault();
    });
  });

  // fechar menus "⋮" ao clicar fora
  document.addEventListener('click', function (e) {
    document.querySelectorAll('details.menu[open]').forEach(function (d) {
      if (!d.contains(e.target)) d.removeAttribute('open');
    });
  });

  // mostrar/ocultar senha
  var olho = document.getElementById('olho'), senha = document.getElementById('senha');
  if (olho && senha) {
    olho.addEventListener('click', function () {
      var ver = senha.type === 'password';
      senha.type = ver ? 'text' : 'password';
      olho.setAttribute('aria-label', ver ? 'Ocultar senha' : 'Mostrar senha');
    });
  }

  // filtro da lista envia sozinho
  var filtro = document.getElementById('filtro-status');
  if (filtro) filtro.addEventListener('change', function () { document.getElementById('form-busca').submit(); });

  // preferências de notificação salvam sozinhas
  var fn = document.getElementById('form-notif');
  if (fn) {
    fn.addEventListener('change', function (e) {
      if (e.target.id === 'chk-sistema' && e.target.checked && 'Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission().then(function () { fn.submit(); });
      } else { fn.submit(); }
    });
  }

  // área de envio do PDF
  var dz = document.getElementById('dropzone');
  if (dz) {
    var input = document.getElementById('pdf');
    var titulo = document.getElementById('dz-titulo');
    var sub = document.getElementById('dz-sub');
    var nome = document.getElementById('nome');
    function mostrar() {
      if (input.files && input.files[0]) {
        var f = input.files[0];
        titulo.textContent = f.name;
        sub.textContent = (f.size / 1048576).toFixed(1) + ' MB — pronto para resumir';
        dz.classList.add('tem-arquivo');
        if (nome && !nome.value) nome.placeholder = f.name.replace(/\.pdf$/i, '').replace(/_/g, ' ');
      }
    }
    input.addEventListener('change', mostrar);
    ['dragenter', 'dragover'].forEach(function (ev) {
      dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.add('arrastando'); });
    });
    ['dragleave', 'drop'].forEach(function (ev) {
      dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.remove('arrastando'); });
    });
    dz.addEventListener('drop', function (e) {
      if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; mostrar(); }
    });
    document.getElementById('form-novo').addEventListener('submit', function () {
      var b = document.getElementById('btn-enviar');
      b.disabled = true; b.textContent = 'Enviando…';
    });
  }

  // acompanhar resumos em andamento
  var itens = document.querySelectorAll('.rcard[data-status="fila"], .rcard[data-status="processando"]');
  if (itens.length) {
    var t = setInterval(function () {
      fetch('/api/jobs', { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (lista) {
        var mapa = {}; lista.forEach(function (j) { mapa[j.id] = j; });
        var concluiu = false, mudou = false;
        itens.forEach(function (el) {
          var j = mapa[el.dataset.id];
          if (!j) return;
          if (j.status !== el.dataset.status && (j.status === 'concluido' || j.status === 'erro')) {
            mudou = true; if (j.status === 'concluido') concluiu = true;
          }
          var p = el.querySelector('progress');
          if (p) { p.max = j.total || 1; p.value = j.progresso; }
        });
        if (mudou) {
          clearInterval(t);
          if (concluiu && document.body.dataset.notif === '1' && 'Notification' in window && Notification.permission === 'granted') {
            try { new Notification('ResumoPro', { body: 'Seu resumo está pronto!' }); } catch (e) {}
          }
          setTimeout(function () { window.location.reload(); }, 600);
        }
      }).catch(function () {});
    }, 2500);
  }
})();
