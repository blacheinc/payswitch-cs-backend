import asyncio
import uuid
import httpx
import json
import logging
from azure.servicebus.management import ServiceBusAdministrationClient
from azure.servicebus.aio import ServiceBusClient
from app.config import get_settings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sb_verify")

BASE_URL = "http://localhost:8000"


async def run_verify():
    settings = get_settings()
    sb_conn_str = settings.azure_servicebus_connection_string
    topic_name = "data-awaits-ingestion"

    # 1. Create a unique subscription for this test
    sub_name = f"verify-today-{uuid.uuid4().hex[:6]}"
    logger.info(f"Creating temporary subscription: {sub_name}")
    admin_client = ServiceBusAdministrationClient.from_connection_string(sb_conn_str)

    # Check if topic exists just in case
    admin_client.create_subscription(topic_name, sub_name)

    try:
        # 2. Perform API Upload
        logger.info("Performing API Upload...")
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=60.0) as client:
            # Admin Login (unified login endpoint)
            login_resp = await client.post(
                "/auth/login",
                json={"email": "admin@example.com", "password": "AdminPassword123!"},
            )
            if login_resp.status_code != 200:
                logger.error(f"Login failed: {login_resp.text}")
                return

            token = login_resp.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            # Create Data Source
            source_resp = await client.post(
                "/admin/sources",
                json={
                    "name": f"Verify Today {sub_name}",
                    "description": "Temporary source for Service Bus verification",
                    "data_type": "historical_loans",
                },
                headers=headers,
            )
            if source_resp.status_code != 201:
                logger.error(f"Source creation failed: {source_resp.text}")
                return

            source_id = source_resp.json()["id"]

            # Upload File
            file_name = f"today_{uuid.uuid4().hex[:4]}.csv"
            file_content = b"id,amount,status\n1,100,paid\n"
            files = {"file": (file_name, file_content, "text/csv")}
            upload_resp = await client.post(
                "/admin/training-data/upload",
                headers=headers,
                data={"source_id": source_id},
                files=files,
            )

            if upload_resp.status_code != 201:
                logger.error(f"Upload failed: {upload_resp.text}")
                return

            upload_id = upload_resp.json()["id"]
            logger.info(f"Upload successful! Upload ID: {upload_id}")

        # 3. Peek Service Bus for the message
        logger.info("Waiting 10 seconds for message to propagate...")
        await asyncio.sleep(10)

        logger.info(f"Peeking messages from subscription {sub_name}...")
        sb_client = ServiceBusClient.from_connection_string(sb_conn_str)
        async with sb_client:
            async with sb_client.get_subscription_receiver(
                topic_name, sub_name, max_wait_time=10
            ) as receiver:
                messages = await receiver.receive_messages(
                    max_message_count=10, max_wait_time=10
                )

                if not messages:
                    logger.error("❌ NO MESSAGES RECEIVED IN SERVICE BUS!")
                else:
                    found = False
                    for msg in messages:
                        body = str(msg)
                        logger.info(f"Found message: {body}")
                        if upload_id in body:
                            logger.info(
                                "✅ SUCCESS: Found today's message in Service Bus!"
                            )
                            logger.info(f"Enqueued Time: {msg.enqueued_time_utc}")
                            found = True
                        await receiver.complete_message(msg)

                    if not found:
                        logger.warning(
                            "Message received but didn't match our Upload ID!"
                        )

    except Exception as e:
        logger.error(f"Verification script error: {e}", exc_info=True)
    finally:
        # 4. Cleanup
        logger.info(f"Deleting temporary subscription: {sub_name}")
        try:
            admin_client.delete_subscription(topic_name, sub_name)
        except:
            pass
        admin_client.close()


if __name__ == "__main__":
    asyncio.run(run_verify())
