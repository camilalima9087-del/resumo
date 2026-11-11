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
  // O arquivo é copiado para a memória assim que é escolhido. Isso evita o erro
  // ERR_UPLOAD_FILE_CHANGED do Chrome no celular (arquivos do Drive/WhatsApp que mudam ou somem antes do envio).
  var dz = document.getElementById('dropzone');
  if (dz) {
    var input = document.getElementById('pdf');
    var titulo = document.getElementById('dz-titulo');
    var sub = document.getElementById('dz-sub');
    var nome = document.getElementById('nome');
    var form = document.getElementById('form-novo');
    var btn = document.getElementById('btn-enviar');
    var textoBtn = btn.innerHTML;
    var arquivoMem = null;

    function aviso(msg) { sub.textContent = msg; dz.classList.remove('tem-arquivo'); }
    function mostrar(f) {
      arquivoMem = null;
      titulo.textContent = f.name;
      sub.textContent = 'Preparando o arquivo…';
      btn.disabled = true;
      var leitura = f.arrayBuffer ? f.arrayBuffer() : Promise.reject(new Error('sem suporte'));
      leitura.then(function (buf) {
        arquivoMem = new File([buf], f.name, { type: 'application/pdf' });
        var kb = buf.byteLength / 1024;
        sub.textContent = (kb >= 1024 ? (kb / 1024).toFixed(1) + ' MB' : Math.max(1, Math.round(kb)) + ' KB') + ' — pronto para resumir';
        dz.classList.add('tem-arquivo');
        if (nome && !nome.value) nome.placeholder = f.name.replace(/\.pdf$/i, '').replace(/_/g, ' ');
        btn.disabled = false;
      }).catch(function () {
        arquivoMem = null;
        titulo.textContent = 'Não foi possível ler este arquivo';
        aviso('Salve o PDF na memória do celular (pasta Downloads) e escolha-o de lá.');
        btn.disabled = true;
      });
    }
    input.addEventListener('change', function () { if (input.files && input.files[0]) mostrar(input.files[0]); });
    ['dragenter', 'dragover'].forEach(function (ev) {
      dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.add('arrastando'); });
    });
    ['dragleave', 'drop'].forEach(function (ev) {
      dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.remove('arrastando'); });
    });
    dz.addEventListener('drop', function (e) {
      if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; mostrar(e.dataTransfer.files[0]); }
    });

    form.addEventListener('submit', function (e) {
      if (!arquivoMem) return;            // sem cópia em memória: envio normal do formulário
      e.preventDefault();
      var fd = new FormData(form);
      fd.set('pdf', arquivoMem, arquivoMem.name);
      var xhr = new XMLHttpRequest();
      xhr.open('POST', form.action);
      xhr.setRequestHeader('X-Fetch', '1');
      btn.disabled = true; btn.textContent = 'Enviando… 0%';
      xhr.upload.onprogress = function (ev) {
        if (ev.lengthComputable) btn.textContent = 'Enviando… ' + Math.round(ev.loaded / ev.total * 100) + '%';
      };
      xhr.onload = function () {
        var destino = '/resumos';
        try { destino = JSON.parse(xhr.responseText).destino || destino; } catch (err) {}
        window.location.href = destino;
      };
      xhr.onerror = xhr.ontimeout = function () {
        btn.disabled = false; btn.innerHTML = textoBtn;
        window.alert('Falha no envio. Verifique a internet e tente de novo.');
      };
      xhr.send(fd);
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
