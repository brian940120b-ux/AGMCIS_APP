let equityChart=null;

function renderEquity(data){
  if(!data.portfolio || !data.portfolio.equity_curve) return;

  const ctx=document.getElementById("equityChart");
  if(!ctx) return;

  const curve=data.portfolio.equity_curve;
  const labels=curve.map(x=>x.index);
  const values=curve.map(x=>x.balance);

  if(equityChart) equityChart.destroy();

  equityChart=new Chart(ctx,{
    type:"line",
    data:{
      labels:labels,
      datasets:[{
        label:"Balance",
        data:values,
        tension:0.25
      }]
    },
    options:{
      responsive:true,
      plugins:{legend:{display:true}}
    }
  });
}
