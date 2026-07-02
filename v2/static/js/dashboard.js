async function loadHealth(){
  const el = document.getElementById("health");

  try{
    const res = await fetch("/health");
    const data = await res.json();

    el.innerHTML = `
      <h2>System Health</h2>
      <p>App：${data.app}</p>
      <p>Version：${data.version}</p>
      <p>Status：${data.status}</p>
    `;
  }catch(e){
    el.innerHTML = "Health API Error";
  }
}

loadHealth();
