let loadChart = document.getElementById('loadChart').getContext('2d');

get_load().then(function (returndata) {

    let lineChart = new Chart(loadChart, {
        type: 'line', // bar, horizontalBar, pie, line, doughnut, radar, polarArea
        data: {
            labels: returndata.time,
            datasets: [{
                    label: 'CPU',
                    data: returndata.cpu,
                    borderColor: 'rgba(255, 127, 64, 1)',
                    backgroundColor: 'rgba(255, 127, 64, 0.2)',
                    borderWidth: 2,
                    hoverBorderWidth: 8,
                    hoverBorderColor: 'rgba(255, 127, 64, 1)',
                    fill: true,
                    cubicInterpolationMode: 'monotone'
                },
                {
                    label: 'MEM',
                    data: returndata.mem,
                    borderColor: 'rgba(64,127,255,1)',
                    backgroundColor: 'rgba(64,127,255, 0.2)',
                    borderWidth: 2,
                    hoverBorderWidth: 8,
                    hoverBorderColor: 'rgba(64,127,255,1)',
                    fill: true,
                    cubicInterpolationMode: 'monotone'
                }
            ]
        },
        options: {
            resonsive: true,
            title: {
                display: false,
                text: 'CPU / Memory utilization percentage',
                fontSize: 25
            },
            legend: {
                display: true,
                position: 'right',
                labels: {
                    fontColor: '#000'
                }
            },
            layout: {
                padding: {
                    left: 50,
                    right: 0,
                    bottom: 0,
                    top: 0
                }
            },
            scales: {
                y: {
                    min: 0,
                    max: 100,
                }
            },
            tooltips: {
                enabled: true
            }
        }
    });
});

function get_load() {
    let url = window.location.origin + "/get_load/";

    return $.getJSON(url).then(function (data) {

        return {
            time: data.timestamp,
            cpu: data.cpu_percentage,
            mem: data.memory_percentage
        }
    })
}