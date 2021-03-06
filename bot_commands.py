from pluginloader import PluginLoader


class Command(object):
    def __init__(self, client, store, config, command, room, event, plugin_loader: PluginLoader):
        """A command made by a user

        Args:
            client (nio.AsyncClient): The client to communicate to matrix with

            store (Storage): Bot storage

            config (Config): Bot configuration parameters

            command (str): The command and arguments

            room (nio.rooms.MatrixRoom): The room the command was sent in

            event (nio.events.room_events.RoomMessageText): The event describing the command
        """
        self.client = client
        self.store = store
        self.config = config
        self.command = command
        self.room = room
        self.event = event
        self.args = self.command.split()[1:]
        self.plugin_loader: PluginLoader = plugin_loader

    async def process(self):

        await self.plugin_loader.run_command(self)
