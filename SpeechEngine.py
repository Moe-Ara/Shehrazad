import pyttsx3
import speech_recognition as sr

recognizer = sr.Recognizer()


def speakText(engine, text):
    engine.say(text)
    engine.runAndWait()


def detectText():
    with sr.Microphone() as mic:
        recognizer.adjust_for_ambient_noise(mic, duration=0.2)
        audio = recognizer.listen(mic)
        MyText = recognizer.recognize_google(audio)
        MyText = MyText.lower()
        # ! Debug
        print(MyText)
    return MyText


def main():
    running = True
    while running:
        engine = pyttsx3.init()
        txt = detectText()
        speakText(engine, txt)
        if txt == "stop":
            running = False
    print("stopped")


if __name__ == "__main__":
    main()
