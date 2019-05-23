var taskSocket = new ReconnectingWebSocket('ws://' + window.location.host + '/ws/task/');
// taskSocket.timeoutInterval = 5400;
function connect() {
    taskSocket.onopen = function open() {
        console.log('WebSockets connection created.');
        taskSocket.send(JSON.stringify({
            "event": "JOIN",
            "message": "sm"
        }));
    };

    if (taskSocket.readyState == WebSocket.OPEN) {
        taskSocket.onopen();
    }

    taskSocket.onclose = function (e) {
        console.log('Socket is closed. Reconnect will be attempted in 1 second.', e.reason);
        taskSocket = new WebSocket('ws://' + window.location.host + '/ws/task/');
        setTimeout(function () {
            connect();
        }, 1000);
    };

    taskSocket.onmessage = function (e) {
        var data = JSON.parse(e.data);
        data = data["payload"];
        let event = data["event"];
        let message = data["message"];
        switch (event) {
            case "NEW_TASK":
                display_new_task(taskSocket, message);
                break;
            case "LIST_STATES_REPLY":
                display_states(taskSocket, message);
                break;
            case "UPDATE_STATE":
                update_state(taskSocket, message);
                break;
            case "TASK_CANCELLED_ACK":
                delete_task(taskSocket, message);
                break;
            default:
                console.log("No event")
        }
    };
}
connect();


$(document).on('click', ".cancel_task", function () {
    let message = {
        id: $(this).parents("tr").attr("data-id"),
    }
    task_cancel(taskSocket, message);
});



$(document).on('click', ".list_state", function () {
    let message = {
        id: $(this).parents("tr").attr("data-id"),
    }
    list_states(taskSocket, message);
});


$("#task_form").submit(function (e) {
    e.preventDefault();
    var serializedData = $(this).serialize();
    $.ajax({
        type: 'POST',
        url: $(this).attr("post_url"),
        data: serializedData,
        success: function (response) {
            taskSocket.send(JSON.stringify({
                "event": "CREATE_TASK",
                "message": {
                    "task": response["task"],
                }
            }));

            $("#task_form")[0].reset();
            $("#task_modal").modal("hide");
        },
        error: function (response) {
            let errors = response["responseJSON"]["errors"];
            $.each(errors, function (key, val) {
                alert(val[1]);
                console.log(key, val)
            })

            $('#id_title').focus();
        }
    });
});

$('#task_modal').on('shown.bs.modal', function () {
    $('#id_title').focus();
})

function task_cancel(taskSocket, message) {
    taskSocket.send(JSON.stringify({
        "event": "TASK_CANCELLED",
        "message": message
    }));
}

function list_states(taskSocket, message) {
    taskSocket.send(JSON.stringify({
        "event": "LIST_STATES",
        "message": message
    }));
}

function display_new_task(taskSocket, message) {

    $("#sm-tasktable tbody").append(`<tr data-id="${message["task"]["id"]}">
        <td class = "title">${message["task"]["title"]}
            <span class="badge badge-primary badge-pill current_state">
                New
            </span>
        </td>
        <td>${formatDate(new Date(message["task"]["creation_at"]))}</td>
        <td>
        <button class="btn btn-secondary btn-sm list_state">List States</button><br>
        <button class="btn btn-danger btn-sm cancel_task">Cancel Task</button>
        </td>
    </tr>`)
}

function display_states(taskSocket, message) {
    let content = "";
    $("#list_task_title").html(message["title"])
    $.each(message["state"], function (i, item) {
        content += `
        ${ ((i == 0 || i%3 == 0) && "<div class = 'row margin-below'>") || "" }
        <div class = 'col-4'>
            <div class = 'card'>
                <div class = "card-header">
                    State - ${i+1}
                </div>
                <div class = 'card-body'>
                    <p> A state <span class = 'font-weight-bold text-success '>
                    ${item["state"]}</span>
                    created at <span class = 'font-weight-bold text-warning'>
                        ${formatDate(new Date(item["at"]))}
                    </span> by <span class ='text-info font-weight-bold'>
                    ${item["by"] || ""} </span>
                    </p>
                </div>
            </div>
        </div>
        ${ ((i != 0 && (i+1)%3 == 0) && "</div>") || "" }
        `;
    })
    content += "</div>";
    $("#list_state_body").html(content);
    $("#list_state_modal").modal("show")
}

function update_state(taskSocket, message) {
    $(`tr[data-id=${message["id"]}] button.cancel_task`).remove();
    $(`tr[data-id=${message["id"]}] span.current_state`).html(message["state"]);
}

function delete_task(taskSocket, message) {
    $(`tr[data-id=${message["id"]}]`).remove();
}