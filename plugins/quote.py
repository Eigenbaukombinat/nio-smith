from nio import AsyncClient, UnknownEvent

from plugin import Plugin
from typing import Dict, List, Tuple
import time
import random
import re
from shlex import split

import logging
logger = logging.getLogger(__name__)

quote_attributes: List[str] = ["user", "members"]
"""valid attributes to select quotes by"""

current_version: int = 2
plugin = Plugin("quote", "General", "Store (more or less) funny quotes and access them randomly or by search term")


def setup():
    """
    Register commands and hooks
    :return:
    """

    plugin.add_command("quote", quote_command, "Post quotes, either randomly, by id, or by search string")
    # plugin.add_command("quote_detail", quote_detail_command, "View a detailed output of a specific quote")
    plugin.add_command("quote_add", quote_add_command, "Add a quote")
    plugin.add_command("quote_del", quote_delete_command, "Delete a quote (can be restored)")
    plugin.add_command("quote_restore", quote_restore_command, "Restore a quote")
    plugin.add_command("quote_links", quote_links_command, "Toggle automatic nickname linking")
    plugin.add_command("quote_replace", quote_replace_command, "Replace a specific quote with the supplied text - destructive, can not be reverted")
    plugin.add_command("quote_upgrade", upgrade_quotes, "Upgrade all Quotes to the most recent version")
    plugin.add_hook("m.reaction", quote_add_reaction)


class QuoteLine:

    def __init__(self, nick: str, message: str, message_type: str = "message"):
        """
        A specific line of a quote
        :param nick: the person's nickname
        :param message: the actual message
        :param message_type: type of the quote, currently either "message" or "action"
        """

        self.nick: str = nick
        self.message: str = message
        self.message_type: str = message_type


class Quote:

    def __init__(self, quote_type: str = "local", text: str = "", url: str = "",
                 channel: str = "", mxroom: str = "",
                 user: str = "", mxuser: str = "",
                 date: float = time.time(),
                 lines: List[QuoteLine] = [],
                 ):
        """
        A textual quote and all its parameters
        :param quote_type: type of the quote (local, remote)
        :param text: the actual text of the local quote, usually a single line or a conversation in multiple lines
        :param url: an url to a remote quote
        :param channel: (legacy) IRC-channel name, room-Name for Quotes added in matrix
        :param mxroom: matrix room id
        :param user: (legacy) IRC-username of the user who added the quote
        :param mxuser: matrix username of the user who added the quote
        :param lines: text of the quote in separate lines
        """

        try:
            self.id = max(plugin.read_data("quotes").keys())+1
        except KeyError:
            self.id = 1

        """id of the quote, automatically set to currently highest id + 1"""
        self.type: str = quote_type
        self.text: str = text
        self.url: str = url
        self.date: float = date
        self.chan: str = channel
        self.mxroom: str = mxroom
        self.user: str = user
        self.mxuser: str = mxuser
        self.version: int = current_version
        self.lines: List[QuoteLine] = lines

        self.deleted: bool = False
        """Flag to mark a quote as deleted"""

        self.rank: int = 0
        """rank of the quote, used to be the number of times the quote has been displayed"""

        self.reactions: Dict[str, int] = {}
        """Dict of reactions (emoji) and their respective counts a quote has received"""

        self.members: List[str] = []
        """List of people participating in the quote"""

    async def display_text(self, command) -> str:
        """
        Build the default textual representation of a randomly called quote
        :return: the textual representation of the quote
        """

        quote_text: str = ""
        if self.get_version() < 2:
            quote_text = self.text
            """pre nick-detection cleanup"""
            quote_text = quote_text.replace("<@", "<")
            quote_text = quote_text.replace("<+", "<")

            """try to find nicknames"""
            p = re.compile(r'<(\S+)>')
            nick_list: List[str] = p.findall(quote_text)

            """replace problematic characters with their html-representation"""
            quote_text = quote_text.replace("<", "&lt;")
            quote_text = quote_text.replace(">", "&gt;")
            quote_text = quote_text.replace("`", "&#96;")

            """matrix allows us to display quotes as multiline-messages :)"""
            quote_text = quote_text.replace(" | ", "  \n")

            """optionally replace nicknames by userlinks"""
            if plugin.read_data("nick_links"):
                nick: str
                nick_link: str
                for nick in nick_list:
                    if nick_link := await plugin.link_user(command, nick, strictness="fuzzy", fuzziness=55):
                        quote_text = quote_text.replace(f"&lt;{nick}&gt;", nick_link)

        else:
            line: QuoteLine
            for line in self.lines:
                if plugin.read_data("nick_links"):
                    nick_link: str
                    if nick_link := await plugin.link_user(command, line.nick, strictness="fuzzy", fuzziness=80):
                        quote_text += f"{nick_link} {line.message}  \n"
                    else:
                        quote_text += f"&lt;{line.nick}&gt; {line.message}  \n"
                else:
                    quote_text += f"&lt;{line.nick}&gt; {line.message}  \n"

        reactions_text: str = ""
        for reaction, count in self.reactions.items():
            if count == 1:
                reactions_text += f"{reaction} "
            else:
                reactions_text += f"{reaction}({count}) "

        return f"**Quote {self.id}**:  \n{quote_text}  \n\n{reactions_text}"

    async def display_details(self, command) -> str:
        """
        Build the textual output of a quotes' full details
        :return: the detailed textual representation of the quote
        """

        full_text: str = f"{self.display_text(command)}\n  " \
                         f"Date: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.date))}\n" \
                         f"Added by: {self.user} / {self.mxuser}\n" \
                         f"Added on: {self.chan} / {self.mxroom}\n" \
                         f"Rank: {self.rank}\n"
        return full_text

    async def match(self, search_terms: List[str]) -> bool:
        """
        Check if the quote matches a list of search terms
        :param search_terms: list of search terms
        :return:    True, if it matches
                    False, if it does not match
        """

        search_term: str
        for search_term in search_terms:
            if search_term.lower() not in self.text.lower():
                return False
        else:
            return True

    async def quote_add_reaction(self, reaction: str):
        """
        Add a reaction to a quote
        :param reaction: the reaction that should be added to the quote
        :return:
        """

        if reaction in self.reactions.keys():
            self.reactions[reaction] += 1
        else:
            self.reactions[reaction] = 1

    def get_version(self) -> int:
        """
        Returns the current version of the quote
        :return: current version of the quote
        """

        try:
            return self.version
        except AttributeError:
            return 0

    def upgrade(self):
        """
        Upgrade a quote to the most recent version
        :return:
        """

        # Version 0 to current
        if self.get_version() < current_version:
            self.convert_string_to_quote_lines()
            if self.lines and len(self.lines) > 0:
                self.version = current_version
                return True
            else:
                return False

    def convert_string_to_quote_lines(self):
        """
        Convert the textual quote string to separate lines
        :return:
        """

        # split text into lines
        full_lines: List[str] = re.split('\r\n?|\n| [|] ', self.text)
        quote_lines: List[QuoteLine] = []

        for line in full_lines:
            nick: str
            message: str
            message_type: str

            if line != "":
                if line[0] == '*':
                    message_type = "action"
                    nick = line.split(' ')[1]
                    message = ' '.join(line.split(' ')[2:])
                else:
                    message_type = "message"
                    nick = line.split(' ')[0].replace('<', '').replace('>', '')
                    message = ' '.join(line.split(' ')[1:])

                quote_lines.append(QuoteLine(nick, message, message_type))

        self.lines = quote_lines


class TrackedQuote:

    def __init__(self, event_id: str, quote_id: int, timestamp: float = time.time()):
        """
        A tracked quote, consisting of event, quote and timestamp to allow for tracking reactions
        :param event_id: the event_id of the message used by the bot to post the quote
        :param quote_id: the id of the quote
        :param timestamp: the timestamp of when the quote was posted to allow removing outdated event_ids
        """
        self.event_id = event_id
        self.quote_id = quote_id
        self.timestamp = timestamp

    async def is_expired(self, max_age: float):
        """
        Check if the TrackedQuote is older than max_age
        :param max_age: the maximum age in seconds a TrackedQuote may have
        :return:    True, if the quote is older than max_age
                    False, if it is not older than max_age
        """

        if self.timestamp < time.time()-max_age:
            return True
        else:
            return False


async def quote_command(command):
    """
    Display a quote, either randomly selected or by specific id, search terms or attributes
    add the event id to tracked_quotes to allow for tracking reactions
    :param command:
    :return: -
    """

    """Load all active (quote.deleted == False) quotes"""
    quotes: Dict[int, Quote]
    try:
        quotes = plugin.read_data("quotes")
        # TODO: check if this needs fixing
        quotes = dict(filter(lambda item: not item[1].deleted, quotes.items()))
    except KeyError:
        await plugin.reply_notice(command, "Error: no quotes stored. See `help quote` how to use quote")
        return False

    quote_id: int
    quote_object: Quote

    if len(command.args) == 0:
        """no id or search term supplied, randomly select a quote"""
        quote_id, quote_object = random.choice(list(quotes.items()))
        await post_quote(command, quote_object)

    elif len(command.args) == 1 and command.args[0].isdigit():
        """specific quote requested by id"""

        if quote_object := await find_quote_by_id(quotes, int(command.args[0])):
            await post_quote(command, quote_object)
        else:
            await plugin.reply_notice(command, f"Quote {command.args[0]} not found")

    else:
        """Find quote by search term"""

        terms: List[str]
        match_id: int
        match_index: int
        total_matches: int

        if command.args[-1].isdigit():
            """check if a specific match is requested"""
            # list of search terms, keeping quoted substrings
            terms = split(" ".join(command.args[:-1]))
            match_id = int(command.args[-1])
        else:
            terms = split(" ".join(command.args))
            match_id = 0

        try:
            (quote_object, match_index, total_matches) = await find_quote_by_search_term(quotes, terms, match_id)
            await post_quote(command, quote_object, match_index, total_matches)
        except TypeError:
            await plugin.reply_notice(command, f"No quote found matching {terms}")


async def post_quote(command, quote_object: Quote, match_index: int = -1, total_matches: int = -1):
    """
    Post a given quote to the room, storing the event id for later tracking
    :param command:
    :param quote_object: the quote to be posted
    :param match_index: index of the quote in matching quotes if found by search term
    :param total_matches: number of total matches if found by search term
    :return:
    """

    event_id: str

    if match_index != -1:
        event_id = await plugin.reply_notice(command, f"{await quote_object.display_text(command)}  \nMatch {match_index} of {total_matches}")
    else:
        event_id = await plugin.reply_notice(command, f"{await quote_object.display_text(command)}")

    """store the event id of the message to allow for tracking reactions to the last 100 posted quotes"""
    tracked_quotes: List[TrackedQuote]
    try:
        tracked_quotes = plugin.read_data("tracked_quotes")
        while len(tracked_quotes) > 100:
            tracked_quotes.pop()
        tracked_quote = TrackedQuote(event_id, quote_object.id)
        tracked_quotes.insert(0, tracked_quote)
        plugin.store_data("tracked_quotes", tracked_quotes)
    except KeyError:
        plugin.store_data("tracked_quotes", [TrackedQuote(event_id, quote_object.id)])


async def find_quote_by_search_term(quotes: Dict[int, Quote], terms: List[str], match_id: int = 0) -> Tuple[Quote, int, int] or None:
    """
    Search for a matching quote by search terms
    :param quotes: Dict of quotes
    :param terms: search terms the quotes must match
    :param match_id: optionally provide a number to return the n'th match to the search terms
    :return:    If a quote has been found:
                Tuple of
                    the quote that has been found
                    the index of the search result
                    the total search results
    """

    matching_quotes: List[Quote] = []

    for quote_id, quote_object in quotes.items():
        if await quote_object.match(terms):
            matching_quotes.append(quote_object)

    if matching_quotes:
        if int(match_id) != 0 and match_id <= len(matching_quotes):
            return matching_quotes[match_id-1], match_id, len(matching_quotes)
        else:
            match_index: int = random.randint(1, len(matching_quotes))
            return matching_quotes[match_index-1], match_index, len(matching_quotes)
    else:
        return None


async def find_quote_by_id(quotes: Dict[int, Quote], quote_id: int) -> Quote or None:
    """
    Find a quote by its id
    :param quotes: The dict containing all current quotes
    :param quote_id: the id of the quote to find
    :return: the Quote that has been found, None otherwise
    """
    try:
        quote_object: Quote = quotes[quote_id]
        return quote_object
    except KeyError:
        return None


async def find_quote_by_attributes(quotes: Dict[int, Quote], attribute: str, values: List[str]) -> Quote or None:
    """
    Find a quote by its attributes
    :param quotes: The dict containing all current quotes
    :param attribute: the attribute by which to find the quote
    :param values: the values of the attribute the quote has to match
    :return: the Quote that has been found, None otherwise
    """

    # TODO: implement this :)
    return None


async def quote_detail_command(command):
    """
    Display a detailed output of the quote
    :param command:
    :return:
    """

    # TODO: implement this :)
    pass


async def quote_add_command(command):
    """
    Add a new quote
    :param command:
    :return:
    """

    if len(command.args) > 0:
        quote: Quote = await quote_add_or_replace(command)
        await plugin.reply_notice(command, f"Quote {quote.id} added")
    else:
        await plugin.reply_notice(command, "Usage: quote_add <quote_text>")


async def quote_replace_command(command):
    """
    Replace a quote
    :param command:
    :return:
    """

    if len(command.args) > 2 and re.match(r'\d+', command.args[0]) and int(command.args[0]) in plugin.read_data("quotes").keys():
        old_quote_text: str = await plugin.read_data("quotes")[int(command.args[0])].display_text(command)
        quote: Quote = await quote_add_or_replace(command, int(command.args[0]))
        await plugin.reply_notice(command, f"Quote {quote.id} replaced  \n"
                                           f"**Old:**  \n"
                                           f"{old_quote_text}  \n\n"
                                           f"**New:**  \n"
                                           f"{await quote.display_text(command)}")
    else:
        await plugin.reply_notice(command, "Usage: quote_replace <quote_id> <quote_text>")


async def quote_add_or_replace(command, quote_id: int = 0) -> Quote or None:
    """

    :param command:
    :param quote_id: optional quote_id to replace an existing quote
    :return: added quote_object or None
    """

    quotes: Dict[int, Quote]
    try:
        quotes = plugin.read_data("quotes")
    except KeyError:
        quotes = {}

    quote_text: str = ""
    new_quote: Quote

    # try to guess formatting
    if command.command.find(' | ') != -1:
        # assume irc-formatting
        quote_text: str
        if quote_id != 0:
            quote_text = " ".join(command.args[1:])
        else:
            quote_text = " ".join(command.args)
        new_quote: Quote = Quote("local", text=quote_text, mxroom=command.room.room_id)
        new_quote.convert_string_to_quote_lines()

    else:
        # assume matrix c&p
        # strip command name
        lines: List[str] = command.command.split(' ', 1)[1].split('\n')
        if quote_id != 0:
            # strip quote from nickname
            lines[0] = lines[0].strip(f"{str(quote_id)} ")
        index: int = 0
        quote_lines: List[QuoteLine] = []
        while index < len(lines)-1:
            quote_lines.append(QuoteLine(lines[index], lines[index+1]))
            quote_text += f"<{lines[index]}> {lines[index+1]} | "
            index += 2
        quote_text = quote_text.rstrip(' | ')
        new_quote = Quote("local", text=quote_text, mxroom=command.room.room_id, lines=quote_lines)

    if quote_id == 0:
        quotes[new_quote.id] = new_quote
        plugin.store_data("quotes", quotes)
        return quotes[new_quote.id]
    else:
        quotes[quote_id].lines = new_quote.lines
        quotes[quote_id].text = new_quote.text
        plugin.store_data("quotes", quotes)
        return quotes[quote_id]


async def quote_delete_command(command):
    """
    Handle the command to delete a quote (sets quote.deleted-flag to True)
    :param command: Command containing the delete_command
    :return:
    """

    quotes: Dict[int, Quote]
    try:
        quotes = plugin.read_data("quotes")
    except KeyError:
        quotes = {}

    if len(command.args) > 1:
        await plugin.reply_notice(command, f"Usage: quote_delete <quote_id>")

    elif len(command.args) == 1 and command.args[0].isdigit():
        quote_id: int = int(command.args[0])
        try:
            if not quotes[quote_id].deleted:
                quotes[quote_id].deleted = True
                plugin.store_data("quotes", quotes)
                await plugin.reply_notice(command, f"Quote {quote_id} deleted")
        except KeyError:
            await plugin.reply_notice(command, f"Quote {quote_id} not found")
    else:
        await plugin.reply_notice(command, f"Usage: quote_delete <id>")


async def quote_restore_command(command):
    """
    Handle the command to restore a quote (sets the quote.deleted-flag to False)
    :param command: Command containing the restore_command
    :return:
    """

    quotes: Dict[int, Quote]
    try:
        quotes = plugin.read_data("quotes")
    except KeyError:
        quotes = {}

    if len(command.args) > 1:
        await plugin.reply_notice(command, f"Usage: quote_restore <quote_id>")

    elif len(command.args) == 1 and command.args[0].isdigit():
        quote_id: int = int(command.args[0])
        try:
            if quotes[quote_id].deleted:
                quotes[quote_id].deleted = False
                plugin.store_data("quotes", quotes)
                await plugin.reply_notice(command, f"Quote {quote_id} restored")
        except KeyError:
            await plugin.reply_notice(command, f"Quote {quote_id} not found")
    else:
        await plugin.reply_notice(command, f"Usage: quote_restore <id>")


async def quote_links_command(command):
    """
    Toggle linking of nicknames on or off
    :param command:
    :return:
    """

    try:
        plugin.store_data("nick_links", not plugin.read_data("nick_links"))

    except KeyError:
        plugin.store_data("nick_links", False)

    await plugin.reply_notice(command, f"Nick linking {plugin.read_data('nick_links')}")


async def quote_add_reaction(client: AsyncClient, room_id: str, event: UnknownEvent):
    """
    Adds reactions to quotes if their event id is known (and tracked in tracked_messages)
    :param client: AsyncClient:
    :param room_id: str:
    :param event: UnknownEvent
    :return:
    """

    quotes: Dict[int, Quote]
    try:
        quotes = plugin.read_data("quotes")
    except KeyError:
        quotes = {}

    tracked_quotes: List[TrackedQuote]
    try:
        tracked_quotes = plugin.read_data("tracked_quotes")
    except KeyError:
        return

    relates_to: str = event.source['content']['m.relates_to']['event_id']
    reaction: str = event.source['content']['m.relates_to']['key']
    quote_id: int = -1

    for tracked_quote in tracked_quotes:
        if relates_to == tracked_quote.event_id:
            quote_id = tracked_quote.quote_id
            break

    if quote_id != -1:
        quote_object: Quote = await find_quote_by_id(quotes, quote_id)
        await quote_object.quote_add_reaction(reaction)
        quotes[quote_id] = quote_object
        plugin.store_data("quotes", quotes)


async def upgrade_quotes(command):
    """
    Upgrade all quotes to the most recent version
    :return:
    """

    quotes: Dict[int, Quote]
    try:
        quotes = plugin.read_data("quotes")

    except KeyError:
        quotes = {}

    upgraded_quotes: int = 0
    upgrade_successful: bool = True
    for quote in quotes.values():
        if quote.get_version() < current_version:
            if not quote.upgrade():
                upgrade_successful = False
            else:
                upgraded_quotes += 1

    if upgrade_successful:
        plugin.store_data("quotes", quotes)
        plugin.store_data("store_version", current_version)
        await plugin.reply_notice(command, f"Success: upgraded {upgraded_quotes} of {len(quotes)} Quotes to Version {current_version}")
    else:
        await plugin.reply_notice(command, f"Error: upgraded {upgraded_quotes} of {len(quotes)} Quotes to Version {current_version}")

setup()
