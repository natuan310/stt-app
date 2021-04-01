import threading
import time
import random

import os
from tkinter import *
import tkinter as tk
from tkinter import Frame, Message, ttk

from subprocess import Popen, PIPE

from google.cloud import speech
import pyaudio
from six.moves import queue


# Audio recording parameters
STREAMING_LIMIT = 240000  # 4 minutes
SAMPLE_RATE = 16000
CHUNK_SIZE = int(SAMPLE_RATE / 10)  # 100ms


os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "./gcp.json"


def get_current_time():
    """Return Current Time in MS."""

    return int(round(time.time() * 1000))


class ResumableMicrophoneStream:  # this class will generate microphone voice in real time
    """Opens a recording stream as a generator yielding the audio chunks."""

    def __init__(self, rate, chunk_size):
        self._rate = rate
        self.chunk_size = chunk_size
        self._num_channels = 1
        self._buff = queue.Queue()
        self.closed = True
        self.start_time = get_current_time()
        self.restart_counter = 0
        self.audio_input = []
        self.last_audio_input = []
        self.result_end_time = 0
        self.is_final_end_time = 0
        self.final_request_end_time = 0
        self.bridging_offset = 0
        self.last_transcript_was_final = False
        self.new_stream = True
        self._audio_interface = pyaudio.PyAudio()
        self._audio_stream = self._audio_interface.open(
            format=pyaudio.paInt16,
            channels=self._num_channels,
            rate=self._rate,
            input=True,
            frames_per_buffer=self.chunk_size,
            # Run the audio stream asynchronously to fill the buffer object.
            # This is necessary so that the input device's buffer doesn't
            # overflow while the calling thread makes network requests, etc.
            stream_callback=self._fill_buffer,
        )

    def __enter__(self):

        self.closed = False
        return self

    def __exit__(self, type, value, traceback):

        self._audio_stream.stop_stream()
        self._audio_stream.close()
        self.closed = True
        # Signal the generator to terminate so that the client's
        # streaming_recognize method will not block the process termination.
        self._buff.put(None)
        self._audio_interface.terminate()

    def _fill_buffer(self, in_data, *args, **kwargs):
        """Continuously collect data from the audio stream, into the buffer."""

        self._buff.put(in_data)
        return None, pyaudio.paContinue

    def generator(self):
        """Stream Audio from microphone to API and to local buffer"""

        while not self.closed:
            data = []

            if self.new_stream and self.last_audio_input:

                chunk_time = STREAMING_LIMIT / len(self.last_audio_input)

                if chunk_time != 0:

                    if self.bridging_offset < 0:
                        self.bridging_offset = 0

                    if self.bridging_offset > self.final_request_end_time:
                        self.bridging_offset = self.final_request_end_time

                    chunks_from_ms = round(
                        (self.final_request_end_time - self.bridging_offset)
                        / chunk_time
                    )

                    self.bridging_offset = round(
                        (len(self.last_audio_input) - chunks_from_ms) * chunk_time
                    )

                    for i in range(chunks_from_ms, len(self.last_audio_input)):
                        data.append(self.last_audio_input[i])

                self.new_stream = False

            # Use a blocking get() to ensure there's at least one chunk of
            # data, and stop iteration if the chunk is None, indicating the
            # end of the audio stream.
            chunk = self._buff.get()
            self.audio_input.append(chunk)

            if chunk is None:
                return
            data.append(chunk)
            # Now consume whatever other data's still buffered.
            while True:
                try:
                    chunk = self._buff.get(block=False)

                    if chunk is None:
                        return
                    data.append(chunk)
                    self.audio_input.append(chunk)

                except queue.Empty:
                    break

            yield b"".join(data)


def listen_print_loop(responses, stream):  # convert voice into text print the data
    """Iterates through server responses and prints them.
    The responses passed is a generator that will block until a response
    is provided by the server.
    Each response may contain multiple results, and each result may contain
    multiple alternatives; for details, see https://goo.gl/tjCPAU.  Here we
    print only the transcription for the top alternative of the top result.
    In this case, responses are provided for interim results as well. If the
    response is an interim one, print a line feed at the end of it, to allow
    the next result to overwrite it, until the response is a final one. For the
    final one, print a newline to preserve the finalized transcription.
    """
    for response in responses:

        if get_current_time() - stream.start_time > STREAMING_LIMIT:
            stream.start_time = get_current_time()
            break

        if not response.results:
            continue

        result = response.results[0]

        if not result.alternatives:
            continue

        transcript = result.alternatives[0].transcript

        result_seconds = 0
        result_micros = 0

        if result.result_end_time.seconds:
            result_seconds = result.result_end_time.seconds

        if result.result_end_time.microseconds:
            result_micros = result.result_end_time.microseconds

        stream.result_end_time = int(
            (result_seconds * 1000) + (result_micros / 1000))

        corrected_time = (
            stream.result_end_time
            - stream.bridging_offset
            + (STREAMING_LIMIT * stream.restart_counter)
        )
        # Display interim results, but with a carriage return at the end of the
        # line, so subsequent lines will overwrite them.

        if result.is_final:
            print('FINAL')
            transcripts_box.insert(
                END, "\n" + str(corrected_time) + ": " + transcript)
            transcripts_box.see(END)

            stream.is_final_end_time = stream.result_end_time
            stream.last_transcript_was_final = True

            # Exit recognition if any of the transcribed phrases could be
            # one of our keywords.
            if re.search(r"\b(exit|quit)\b", transcript, re.I):
                transcripts_box.insert(END, "\nExiting...\n")
                transcripts_box.update_idletasks()

                stream.closed = True
                break

        else:
            live_trans_msg.delete("1.0", "end")
            live_trans_msg.insert(
                END, "\033" + str(corrected_time) + ": " + transcript + "\r")
            live_trans_msg.update_idletasks()

            stream.last_transcript_was_final = False


def main():
    """start bidirectional streaming from microphone input to speech API"""
    mtg_name = meeting_name.get()

    # transcripts_box.configure(state=NORMAL)
    client = speech.SpeechClient()
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=SAMPLE_RATE,
        language_code="en-US",
        max_alternatives=1,
    )

    streaming_config = speech.StreamingRecognitionConfig(
        config=config, interim_results=True
    )

    mic_manager = ResumableMicrophoneStream(
        SAMPLE_RATE, CHUNK_SIZE)  # real time voice

    transcripts_box.insert(
        END, f'\n{mtg_name} - Start recording.\n')
    # transcripts_box.insert(END, "End (ms)       Transcript Results/Status\n")
    transcripts_box.insert(
        END, "=====================================================")
    transcripts_box.update_idletasks()

    with mic_manager as stream:

        while not stream.closed:

            transcripts_box.insert(
                END, "\n" + str(STREAMING_LIMIT * stream.restart_counter) + f"{mtg_name} contents\n")
            transcripts_box.update_idletasks()

            stream.audio_input = []
            audio_generator = stream.generator()

            requests = (
                speech.StreamingRecognizeRequest(audio_content=content)
                for content in audio_generator
            )

            responses = client.streaming_recognize(streaming_config, requests)

            # Now, put the transcription responses to use.
            a = listen_print_loop(responses, stream)
            transcripts_box.insert(END, str(a))
            transcripts_box.update_idletasks()
            # transcripts_box.configure(state=DISABLED)
            if stream.result_end_time > 0:
                stream.final_request_end_time = stream.is_final_end_time
            stream.result_end_time = 0
            stream.last_audio_input = []
            stream.last_audio_input = stream.audio_input
            stream.audio_input = []
            stream.restart_counter = stream.restart_counter + 1

            if not stream.last_transcript_was_final:

                transcripts_box.insert(END, "\n")
                transcripts_box.update_idletasks()

            stream.new_stream = True


# Desktop UI using Python Tkinter∫
window = tk.Tk()
window.title('Sutrix Solutions  - Speech-To-Text')
# window.geometry("800x1000")
window.grid_rowconfigure(0, weight=1)
window.grid_columnconfigure(0, weight=1)

main_frame = tk.Frame(window)
main_frame.grid(column=0, row=0, sticky = "nsew")
# main_frame.grid_rowconfigure(0, weight = 1)
# main_frame.grid_columnconfigure(0, weight = 1)

header = ttk.Label(
    main_frame, text=""" Sutrix Solution - Speech-To-Text Desktop App""")
header.grid(row=0, column=0, columnspan=8)

meeting_name_lbl = ttk.Label(main_frame, text='Meeting name: ')
meeting_name_lbl.grid(row=1, column=0, columnspan=2)

meeting_name = tk.StringVar()
meeting_name_ent = ttk.Entry(main_frame, width=50, textvariable=meeting_name)
meeting_name_ent.grid(row=1, column=3, columnspan=5)

# start_btn = ttk.Button(main_frame, text="Start transcribe",
#                         command=load_transcript)

start_btn = ttk.Button(main_frame, text="Start transcribe",
                       command=main)

start_btn.grid(row=2, column=3, columnspan=6, pady=5)

transcriptions = tk.StringVar()
scrol_y = Scrollbar(main_frame, orient=VERTICAL)

live_trans_lbl = ttk.Label(main_frame, text='Live transcript:')
live_trans_lbl.grid(row=3, column=0, columnspan=3)

live_trans = tk.StringVar()
live_trans_msg = tk.Text(main_frame, height=2, width=80, wrap=WORD)
live_trans_msg.grid(row=5, column=0, columnspan=8, rowspan=2)

transcript_lbl = ttk.Label(main_frame, text='Meeting content:')
transcript_lbl.grid(row=7, column=0, columnspan=3)

transcripts_box = tk.Text(
    main_frame, yscrollcommand=scrol_y.set, height=20, width=80, wrap=WORD)
transcripts_box.grid(row=8, column=0, columnspan=8)


window.mainloop()
