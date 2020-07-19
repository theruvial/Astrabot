import discord
from discord.ext import commands
from discord.utils import get
from discord_webhook import DiscordWebhook, DiscordEmbed
from trello import TrelloClient
from trello import card
from config import config
from trelloConfig import trelloConfig
import asyncio
import time

class TrelloToDo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.members = trelloConfig["members"]
        self.webhookList
        self.activeUser
        self.label_colors = trelloConfig["label_colors"]
        self.list_ids = trelloConfig["list_ids"]        
        
        
        

    async def clear_channels(self, text_id):
            channel = self.bot.get_channel(text_id)
            await channel.purge(limit=100)


    async def pullTasks(self, list_id, hook):
        for card in list_id.list_cards():            
            self.embedList = []          
            guild = discord.utils.get(self.bot.guilds, name="Aboard The Astra")

            # Pulls label color out of card and applies it to discord embed
            try:
                for labels in card.labels:
                    if labels.name in self.label_colors.keys():
                        label_color = self.label_colors[labels.name]
            except TypeError:
                label_color = 13158600

            webhook = DiscordWebhook(url = hook)
            embed = DiscordEmbed(title = card.name, url = card.short_url, color = label_color)

            # Pulls label name out of card and applies it to a field on the discord embed
            if not card.labels:
                pass
            else:
                label_list = []
                for label in card.labels:
                    label_list.append(label.name)
                labels = ', '.join(label_list)
                embed.add_embed_field(name = "Labels", value = labels, inline = False)

            

            # Pull description out of card and applies it to a field on the discord embed
            if not card.description:
                pass
            elif len(card.description) > 480:
                ### card.description[:480] + "..."
                embed.add_embed_field(name = 'Description', value = f"Card Description exceeds the character limit,\nPlease reference {card.short_url} for a full description", inline = False)
            else:
                embed.add_embed_field(name = 'Description', value = card.description, inline = False)

            embed.set_timestamp()

            # Pulls member name out of card and applies it to a field on the dicord embed
            if not card.member_ids:
                pass
            else:
                discord_username_list = []
                for trello_user_id in card.member_ids:
                    for member in self.members.keys():
                        if trID in member[trello_user_id]:
                            discord_username_list.append(guild.get_member(trID).mention)
                        else:
                            discord_username_list.append("User not defined.")

                        self.activeUser = discord_username_list[0]
                        discord_user_names = ", ".join(discord_username_list)
                        embed.add_embed_field(name = 'Assigned Users:', value = discord_user_names, inline = False)
                                    
            self.embedList.append(embed)
            webhook.add_embed(embed)
            webhook.execute()
            await asyncio.sleep(2.0)

    @commands.command(pass_context = True, aliases = ["pt", "task", "ut"])
    async def pullCards(self, ctx):
        client = TrelloClient(
            config["tr_api_key"],
            config["tr_api_secret"]
        )
        all_boards = client.list_boards()
        last_board = all_boards[1]        
        to_do = last_board.get_list(self.list_ids["to_do"])        
        
        trello_list = [to_do]

        # Clears channels before webhooks are executed 
        for member in self.members.keys():
            await self.clear_channels(member["channel_id"])            

        # Posts all tasks to the individual channels based on the first name in the discord username list for each card
        for member in self.members.keys():
            for webhook in member["webhook"]:
                if self.activeUser == member["user"]:
                    await pullTasks(trello_list[0], webhook)
def setup(bot):
    bot.add_cog(TrelloToDo(bot))

