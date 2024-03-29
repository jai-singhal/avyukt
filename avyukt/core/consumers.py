from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from django.db import transaction
import json
import logging
from avyukt.core.broker import RabbitMQBroker
from avyukt.core.events import events
from avyukt.core.models import (
    DeliveryTaskState,
    DeliveryTask,
    DeliveryStateTransition
)
from django.db.models import F,  Window
from django.db.models.functions import FirstValue


class DeliveryTaskConsumer(AsyncJsonWebsocketConsumer):
    broker = RabbitMQBroker()
    group_names = {
        "dp": "delivery_person",
        "sm": "store_manager"
    }
    events = events

    async def connect(self):
        requser = self.scope["user"]
        if requser.is_authenticated:
            if requser.is_storage_manager or requser.is_delivery_person:
                await self.accept()
            else:
                await self.close()
        else:
            await self.close()

    async def disconnect(self, close_code):
        # Leave the rooms: discard the group
        requser = self.scope["user"]
        if requser.is_authenticated:
            logging.info(requser.username + ": user disconnected.")
            if requser.is_storage_manager:
                await self.channel_layer.group_discard(
                    "user-%s-%s" % (self.group_names["sm"],
                                    requser.username),
                    self.channel_name,
                )

            if requser.is_delivery_person:
                await self.channel_layer.group_discard(
                    "user-%s-%s" % (self.group_names["dp"],
                                    requser.username),
                    self.channel_name,
                )

    async def receive(self, text_data):
        """
        Recieve the request from the client,
        get the evebt and call the appropriate function
        """
        try:
            response = json.loads(text_data)
            event = response.get("event", None)
            message = response.get("message", None)
            if event == self.events["JOIN"]:
                await self.join(group_name=message)
            elif event == self.events["CREATE_TASK"]:
                await self.create_task(message)
            elif event == self.events["GET_NEW_TASK"]:
                await self.send_task_from_queue(retain=True)
            elif event == self.events["TASK_CANCELLED"]:
                await self.task_cancelled(message)
            elif event == self.events["TASK_ACCEPTED"]:
                await self.task_accepted(message)
            elif event == self.events["TASK_COMPLETED"]:
                await self.task_completed(message)
            elif event == self.events["TASK_DECLINED"]:
                await self.task_declined(message)
            elif event == self.events["LIST_STATES"]:
                await self.list_states(message)

        except Exception as e:
            logging.error(str(e))

    # Helping Methods

    async def join(self, group_name):
        """
        Method to entertain the new connected user,
        create the group for them(One individual group
        and other for sm/dp)
        """
        logging.info(f"New {group_name} joined with id: {self.channel_name}")
        await self.channel_layer.group_add(
            self.group_names[group_name],
            self.channel_name,
        )
        # INdividual person group format: user-{dp/sm}-{username}
        await self.channel_layer.group_add(
            "user-%s-%s" % (self.group_names[group_name],
                            self.scope["user"].username),
            self.channel_name,
        )

        if group_name == "dp":
            await self.send_task_from_queue(retain=True)
        elif group_name == "sm":
            pass

    async def create_task(self, message):
        """
            Call when new task is created by storage manager,
            STEPS
            1. Create new state
            2. Push the state into queue,
            3. Send the message to storage manager about the new 
                created task
            4. Send the task from the queue, to the Delivery Person
        """
        await self.create_state(message["task"]["id"], state="new", by=self.scope["user"])

        await self.broker.basic_publish(message)
        await self.group_send(
            {
                "event": self.events["NEW_TASK"],
                "message": message
            },
            "user-%s-%s" % (self.group_names["sm"],
                            self.scope["user"].username)
        )

        await self.send_task_from_queue(retain=True)

    async def task_cancelled(self, message):
        """
        STEPS: 
            1. Consume the task
            2. Delete the task from the database 
            3. Send the ack and delete the task from table
            4. Send the new task from the queue to delivery person
        """
        task = await self.get_task(message["id"])
        await self.broker.basic_get(queue=task["priority"], auto_ack=True)

        await self.delete_task(message["id"])
        await self.group_send(
            {
                "event": self.events["TASK_CANCELLED_ACK"],
                "message": message
            },
            "user-%s-%s" % (self.group_names["sm"],
                            self.scope["user"].username)
        )
        await self.send_task_from_queue(retain=False)

    async def task_accepted(self, message):
        """
        STEPS: 
            1. GET the total accepted states of current Delivery Person user, 
            and check condition
                If condition(more than 3 pending task) not statisifies
                    1.1. Create new state: Accepted, assign to the task with task_id.
                    1.2. dispatch from the queue, and show next available task 
                    to other dp users
                    1.3. Send a signal to STorage manager ragarding chaneg in state
                Else:
                    - Send a signal to current dp user about exceeding
                     the more than 3 pending state
        """
        total_pending_state = await self.check_total_pending_tasks(
            user=self.scope["user"])

        if not total_pending_state:
            res = await self.create_state(message["id"], state="accepted", by=self.scope["user"])
            if not res:
                return

            payload = {
                "event": self.events["TASK_ACCEPTED"],
                "message": message
            }

            # UPDATE THE STATE SM's END
            await self.group_send(
                {
                    "event": self.events["UPDATE_STATE"],
                    "message": {"id": message["id"], "state": "Accepted"}
                },
                "user-%s-%s" % (self.group_names["sm"], message["created_by"])
            )
            await self.send_task_from_queue(retain=False)

        else:
            payload = {
                "event": self.events["TASK_PENDING"],
                "message": "USER EXCEEDS TOTAL PENDING TASKS"
            }

        await self.group_send(
            payload,
            "user-%s-%s" % (self.group_names["dp"],
                            self.scope["user"].username)
        )

    async def task_declined(self, message):
        """
        STEPS: 
            1. Create new state: declined.
            2. Enqeuue the task back again in queue
            3. Remove the task from the delivery person dashboard
            4. Update the state in the storage manager side,
            and also alert storage manager about the declined task
            5. Send the new task from the queue
        """
        res = await self.create_state(message["id"], state="declined", by=self.scope["user"])
        if not res:
            return
        task = await self.get_task(message["id"])
        await self.broker.basic_publish({"task": task})

        await self.group_send(
            {
                "event": self.events["TASK_DECLINED_ACK"],
                "message": {"id": message["id"]}  # only need id to identify
            },
            "user-%s-%s" % (self.group_names["dp"],
                            self.scope["user"].username)
        )

        # UPDATE THE STATE SM's END
        await self.group_send(
            {
                "event": self.events["UPDATE_STATE"],
                "message": {"id": message["id"], "state": "Declined"}
            },
            "user-%s-%s" % (self.group_names["sm"], task["created_by"])
        )

        await self.group_send(
            {
                "event": self.events["TASK_DECLINED_ACK_SM"],
                "message": {"id": message["id"], "task": task["title"]}
            },
            "user-%s-%s" % (self.group_names["sm"], task["created_by"])
        )

        await self.send_task_from_queue(retain=True)

    async def task_completed(self, message):
        """
        STEPS: 
            1. Create new state: Completed.
            2. Remove the task from user-dp dashboard
            3. Send the ack about the update state
        """
        res = await self.create_state(message["id"], state="completed", by=self.scope["user"])
        if not res:
            return
        await self.group_send(
            {
                "event": self.events["TASK_COMPLETED_ACK"],
                "message": {"id": message["id"]}  # only need id to identify
            },
            "user-%s-%s" % (self.group_names["dp"],
                            self.scope["user"].username)
        )

        # UPDATE THE STATE SM's END
        task = await self.get_task(message["id"])
        await self.group_send(
            {
                "event": self.events["UPDATE_STATE"],
                "message": {"id": message["id"], "state": "Completed"}
            },
            "user-%s-%s" % (self.group_names["sm"], task["created_by"])
        )

    async def list_states(self, message):
        """
        Get all the state of the task to storage manager
        """
        await self.group_send(
            {
                "event": self.events["LIST_STATES_REPLY"],
                "message": await self.get_all_states(task_id=message["id"])
            },
            "user-%s-%s" % (self.group_names["sm"],
                            self.scope["user"].username)
        )

    async def send_task_from_queue(self, retain=False):
        """
        Consume the task from the queue, prioritized by high, medium, and low,
        i.e., take the task from highest queue first,
        If want to retain the task, publish the task back again in the queue
        ANd then send the task(if found) to the delivery person(all), and if not
        found, return null message.
        """
        task = await self.broker.basic_get(queue="high", auto_ack=False)
        if not task:
            task = await self.broker.basic_get(queue="medium", auto_ack=False)
            if not task:
                task = await self.broker.basic_get(queue="low", auto_ack=False)

        if task:
            if retain:
                await self.broker.basic_nack(delivery_tag=task["delivery_tag"])
            else:
                await self.broker.basic_ack(delivery_tag=task["delivery_tag"])
            payload = {
                "event": self.events["NEW_TASK"],
                "message": task["message"],
            }
        else:
            payload = {
                "event": self.events["NEW_TASK"],
                "message": None
            }

        await self.group_send(payload, self.group_names["dp"])

    # Database handler methods
    @transaction.atomic
    @database_sync_to_async
    def create_state(self, task_id, state, by=None):
        """
        Create a new state and a new transition in db
        """
        try:
            state_instance = DeliveryTaskState.objects.get(state=state)

            # consitency check for double click on button
            last_transaction_state = DeliveryStateTransition.objects.filter(
                task_id=task_id,
                by=by
            ).order_by("-at").first()
            if last_transaction_state and last_transaction_state.state_id == state_instance.id:
                return False

            # save the instance of the transaction
            task_instance = DeliveryTask.objects.get(id=task_id)

            transition_instance = DeliveryStateTransition(
                task_id=task_instance.id,
                state_id=state_instance.id,
                by=by
            )
            transition_instance.save()
            task_instance.states.add(state_instance)
            task_instance.save()

            return True

        except Exception as e:
            logging.error(str(e))
            return False

    @transaction.atomic
    @database_sync_to_async
    def check_total_pending_tasks(self, user):
        """
            Returns True if no. of states of dp user which 
            is in pending states(accepted but not completed and declined)
            is greater than 3
            Return False otherwise
        """
        user_tasks = DeliveryTask.objects.prefetch_related("states").filter(
            states__deliverystatetransition__by=user).distinct()
        total_pending_task = 0
        for task in user_tasks:
            latest_state = task.states.order_by("-deliverystatetransition__at").first()
            if latest_state.state == "accepted":
                total_pending_task += 1
        if total_pending_task >= 3:
            return True
        else:
            return False

    @database_sync_to_async
    def get_task(self, task_id):
        # get the task in the form of json
        return DeliveryTask.objects.get_object_in_json(task_id)

    @database_sync_to_async
    def get_all_states(self, task_id):
        # get all the task transitions of a given task
        qs = DeliveryStateTransition.objects.get_states_in_json(
            task_id=task_id)
        return qs

    @database_sync_to_async
    def delete_task(self, task_id):
        # delete the task
        DeliveryTask.objects.get(id=task_id).delete()

    async def group_send(self, message, group):
        """
            Helping method: SENDS the message to the group
        """
        await self.channel_layer.group_send(
            group,
            {
                'type': 'send_message',
                'message': message,
            }
        )

    async def send_message(self, res):
        """
            Callback for group_send()
        """
        await self.send(text_data=json.dumps({
            "payload": res["message"],
        }))
