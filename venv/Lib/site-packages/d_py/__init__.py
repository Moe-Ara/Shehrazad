__version__ = "0.0.3";

import discord;
import os;

class DPY:
  def __init__(self, prefix, message, client):
    self.message = message;
    self.client = client;

  def __getattributes__(self):
    return {"prefix": self.prefix, "message": self.message, "client": self.client};

    # line of code in remembrance of proctoken lmfao

  class msg:
      # SEND A MESSAGE
    async def send(msg):
      """
      sends a message to the channel.
      """
      message = DPY.__getattributes__()["message"];

      await message.channel.send(msg);
    
      # DELETE A MESSAGE
    async def delete():
      """
      deletes a message from the channel.
      """
      message = DPY.__getattributes__()["message"];

      await message.delete(message);
    
    async def edit():
      """
      edits a message sent by the client (bot).
      """    
      message = DPY.__getattributes__()["message"];
      
      await message.edit()
    
    def get_args():
      message = DPY.__getattributes__()["message"];
      pfx = DPY.prefix;
      message.replace(pfx, "");
      args = message.split(" "); args.pop(0);
      return args;
    
    def get_cmd(self):
      args = self.get_args();
      return args[0];

  class Embed:
    def __init__(self, **kwargs):
      if kwargs.hasattr("description"):
        self.description = kwargs["description"];
      else:
        return print("Error here for not adding description");

      if kwargs.hasattr("title"):
        self.title = kwargs["title"];
      else:
        return print("Error here for not adding title")
      
      if kwargs.hasattr("color"):
        self.color = kwargs["color"];

        return discord.Embed(title = self.title, description = self.description, color = self.color);
      else:
        return discord.Embed(title = self.title, description = self.description);
       
  class f_arg:
      # GET AN ID
    def getid(self, x):
      """
      gets user id from a string
      """
      if isinstance(x, str):
        if x.startswith("<@!") and len(x) == 22:
          for i in x:
            if str.isnumeric(i):
              pass;
            else:
              x = x.replace(i, "")
          return x;
        else:
          return print("\033[1;31;40m * dpy warning: getid() req, ment_user has to be user value\033[1;37;40m");
      else:
        return print("\033[1;31;40m * dpy warning: getid() req, ment_user has to be user value\033[1;37;40m");
        
  class f_auth:
      # CHECK FOR ROLE
    def hasrole(self, x):
      message = DPY.__getattributes__()["message"];
      for i in message.author.roles:
        if i.name == x:
          return True;
      return False;
    
      # GET ROLES
    def getroles(self, x):
      message = DPY.__getattributes__()["message"];
      return message.author.roles

