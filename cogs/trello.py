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

class Trello(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.channel_id_dict = {
            'backlog': 630577538997157898,
            'to_do': 630577579950342174,
            'in_progress':  630577634702786560,
            'to_review': 630577675584667648,
            'complete': 630577708899762196,
            'art_to_do': 711669216805257307
        }
        self.trello_to_disc_dict = {
            # Steve's ID
            '58cee4f46044d2ee3dc77fe8': 168897667387097089,
            # Tim's ID
            '5d8d5649109263725d255b0f': 151408413744693248,
            # Justin's ID
            '5b79e488cc93ac833cb2d1d4': 120661885573005316,
            # Kathi's ID
            '5e0ffed18753e146d313424f': 185961347832348673,           
            #Peach's ID
            '5e9c96d3d0bcfa4bf70b427f': 434413462907781120
        }

        self.label_colors = trelloConfig["label_colors"]

    ##This function clears the channels before the webhook updates the cards.
    async def clear_channels(self, text_id):
        channel = self.bot.get_channel(text_id)
        await channel.purge(limit=100)

    async def pullCards(self, trello_list, hook):
        for card in trello_list.list_cards():
            guild = discord.utils.get(self.bot.guilds, name='Aboard The Astra')

            # try:
            #     for labels in card.labels:                    
            #         if labels.name == 'Code':
            #             label_color = 6405455
            #         elif labels.name == 'Unity':
            #             label_color = 15424070
            #         elif labels.name == 'Sound':
            #             label_color = 16752410
            #         elif labels.name == 'Art':
            #             label_color = 15914496
            #         elif labels.name == 'Admin':
            #             label_color = 12810208
            #         elif labels.name == 'Debug':
            #             label_color = 3425635
            #         elif labels.name == 'Writing':
            #             label_color = 31167                    
            # except TypeError:                
            #     label_color = 13158600

            # Pulls label name out of card and applies it to a field on the discord embed
            if not card.labels:
                pass
            else:
                label_list = []
                for label in card.labels:
                    label_list.append(label.name)
                labels = ', '.join(label_list)
                embed.add_embed_field(name = "Labels", value = labels, inline = False)
                                

            webhook = DiscordWebhook(url= hook)
            embed = DiscordEmbed(title = card.name, url = card.short_url, color=label_color)

            if not card.labels:
                pass
            else:
                label_list = []
                for label in card.labels:
                    label_list.append(label.name)
                labels = ', '.join(label_list)
                embed.add_embed_field(name = 'Labels', value = labels, inline = False)

            if not card.member_ids:
                pass
            else:                
                discord_username_list = []
                for trello_user_id in card.member_ids:
                    if trello_user_id in self.trello_to_disc_dict.keys():                        
                        discord_username_list.append(guild.get_member(self.trello_to_disc_dict[trello_user_id]).mention)                            
                    else:
                        discord_username_list.append('This user is not yet defined.')                
                discord_user_names = ", ".join(discord_username_list)                
                embed.add_embed_field(name = 'Assigned Users:', value = discord_user_names, inline = False)

            if not card.description:
                pass
            elif len(card.description) > 480:
                ### card.description[:480] + "..."
                embed.add_embed_field(name = 'Description', value = f"Card Description exceeds the character limit,\nPlease reference {card.short_url} for a full description", inline = False)
            else:
                embed.add_embed_field(name = 'Description', value = card.description, inline = False)

            embed.set_timestamp()            
            webhook.add_embed(embed)
            webhook.execute()
            await asyncio.sleep(2.0)

    @commands.command(pass_context = True, aliases = ['gc','GET','update'])
    async def getcards(self, ctx):
    #Defines the trello client using api keys from config.json
        client = TrelloClient(
            config['tr_api_key'],
            config['tr_api_secret']
            # config['tr_token'],
            # config['tr_token_secret']
        )
        all_boards = client.list_boards()
        # Hardcode last board (i.e. what would happen if you added a new board?)
        last_board = all_boards[1]             
        #Id's for all the board lists
        backlog = last_board.get_list('5e8a3c760854b653f6e199c8')
        to_do = last_board.get_list('5e8a3c87f0f2d34d8f1a8628')
        art_to_do = last_board.get_list('5ec197592808aa0bf51f3a5d')
        in_progress = last_board.get_list('5e8a3c8bbaeabd26fcf65b38')
        to_review = last_board.get_list('5e8a3c8e99b63c4844429850')
        complete = last_board.get_list('5e8a3c9ca413bd314a27967e')
        trello_list = [backlog, to_do, in_progress, to_review, complete, art_to_do]

        for key in self.channel_id_dict.keys():
            await self.clear_channels(self.channel_id_dict[key])

# make this scalable. Probably means renaming the config keys.
        await self.pullCards(trello_list[0], config['tr_backlog'])
        await self.pullCards(trello_list[1], config['tr_to_do'])
        await self.pullCards(trello_list[5], config['tr_art_to_do'])
        await self.pullCards(trello_list[2], config['tr_in_progress'])
        await self.pullCards(trello_list[3], config['tr_to_review'])
        await self.pullCards(trello_list[4], config['tr_complete'])


def setup(bot):
    bot.add_cog(Trello(bot))
