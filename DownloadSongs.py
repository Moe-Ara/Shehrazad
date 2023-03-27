import yt_dlp
import spotify
import Common


async def downloadYoutube(links):
    try:
        options = {
            'format': 'm4a/bestaudio/best',
            ## maybe the path can produce bugs
            'outtmpl': f'{Common.song_path}%(title)s.%(ext)s',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredquality': '192',
            }]
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url=links, download=False)
            await ydl.download(links)
        print(f"Download complete ... {info['title']}")
    except Exception as e:
        print(e)
    # return info['title'] + '.m4a'
    return info['title']


# def downloadYoutube(links):
#     try:
#         options = {
#             'format': 'm4a/bestaudio/best',
#             ## maybe the path can produce bugs
#             'outtmpl': f'{Common.song_path}%(title)s.%(ext)s',
#             # 'postprocessors': [{
#             #     'key': 'FFmpegExtractAudio',
#             #     'preferredcodec': 'm4a',
#             # }]
#         }
#         with yt_dlp.YoutubeDL(options) as ydl:
#             info = ydl.extract_info(url=links,download=False)
#             ydl.download(links)
#         print(f"Download complete ... {info['title']}")
#     except:
#         print("Error Downloading !!!")
#     return info['title']+'.m4a'


# def downloadSpotify(links):
#     vid =

# def playSongs():

if __name__ == '__main__':
    tilte = downloadYoutube(
        'https://www.youtube.com/watch?v=VhC5z-DUlJg')
    print(
        tilte
    )
