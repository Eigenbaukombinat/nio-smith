#!/usr/bin/env python3

import logging
import asyncio
from time import sleep

from nio import (
    AsyncClient,
    AsyncClientConfig,
    RoomMessageText,
    InviteEvent,
    LocalProtocolError, LoginResponse, LoginError)
from callbacks import Callbacks
from config import Config
from storage import Storage
from aiohttp.client_exceptions import (
    ServerDisconnectedError,
    ClientConnectionError)

logger = logging.getLogger(__name__)
client = ""


async def run_plugins(response):
    pass
    # we might need pluginloader hooks here
    # await plugins.sabnzbdapi.watchjobs(client)


async def main():
    # TODO: this really needs to be replaced
    # probably using https://docs.python.org/3.8/library/functools.html#functools.partial
    global client

    # Read config file
    config = Config("config.yaml")

    # Configure the database
    store = Storage(config.database_filepath)

    # Configuration options for the AsyncClient
    client_config = AsyncClientConfig(
        max_limit_exceeded=0,
        max_timeouts=0,
        store_sync_tokens=True,
        encryption_enabled=config.enable_encryption,
    )

    # Initialize the matrix client
    client = AsyncClient(
        config.homeserver_url,
        config.user_id,
        device_id=config.device_id,
        store_path=config.store_filepath,
        config=client_config,
    )

    # Set up event callbacks
    callbacks = Callbacks(client, store, config)
    client.add_event_callback(callbacks.message, (RoomMessageText,))
    client.add_event_callback(callbacks.invite, (InviteEvent,))
    client.add_response_callback(run_plugins)

    # Keep trying to reconnect on failure (with some time in-between)
    while True:
        try:
            # Try to login with the configured username/password
            try:
                login_response = await client.login(
                    password=config.user_password,
                    device_name=config.device_name,
                )

                # Check if login failed
                if type(login_response) == LoginError:
                    logger.error(f"Failed to login: %s", login_response.message)
                    return False
            except LocalProtocolError as e:
                # There's an edge case here where the user enables encryption but hasn't installed
                # the correct C dependencies. In that case, a LocalProtocolError is raised on login.
                # Warn the user if these conditions are met.
                if config.enable_encryption:
                    logger.fatal(
                        "Failed to login and encryption is enabled. Have you installed the correct dependencies? "
                        "https://github.com/poljar/matrix-nio#installation"
                    )
                    return False
                else:
                    # We don't know why this was raised. Throw it at the user
                    logger.fatal("Error logging in: %s", e)

            # Login succeeded!

            # Sync encryption keys with the server
            # Required for participating in encrypted rooms
            if client.should_upload_keys:
                await client.keys_upload()

            logger.info(f"Logged in as {config.user_id}")
            await client.sync_forever(timeout=30000, full_state=True)

        except (ClientConnectionError, ServerDisconnectedError):
            logger.warning("Unable to connect to homeserver, retrying in 15s...")

            # Sleep so we don't bombard the server with login requests
            sleep(15)
        finally:
            # Make sure to close the client connection on disconnect
            await client.close()


asyncio.get_event_loop().run_until_complete(main())
