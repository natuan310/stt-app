from tkinter import *
import tkinter as tk

import threading
import time
import random

import os

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


# class BackgroundTask():

#     def __init__( self, taskFuncPointer ):
#         self.__taskFuncPointer_ = taskFuncPointer
#         self.__workerThread_ = None
#         self.__isRunning_ = False

#     def taskFuncPointer( self ) : return self.__taskFuncPointer_

#     def isRunning( self ) : 
#         return self.__isRunning_ and self.__workerThread_.isAlive()

#     def start( self ): 
#         if not self.__isRunning_ :
#             self.__isRunning_ = True
#             self.__workerThread_ = self.WorkerThread( self )
#             self.__workerThread_.start()

#     def stop( self ) : self.__isRunning_ = False

#     class WorkerThread( threading.Thread ):
#         def __init__( self, bgTask ):      
#             threading.Thread.__init__( self )
#             self.__bgTask_ = bgTask

#         def run( self ):
#             try :
#                 self.__bgTask_.taskFuncPointer()( self.__bgTask_.isRunning )
#             except Exception as e: print repr(e)
#             self.__bgTask_.stop()


class SerialThread(threading.Thread):

    def __init__(self, queue, sp):
        threading.Thread.__init__(self)
        self.queue = queue
        self.event = threading.Event() # An event object.
        self.ser_handle = sp;

    def stop(self):
        self.event.set()

    def run(self):
        while not self.event.isSet():
            if self.ser_handle.inWaiting():
                text = self.ser_handle.readline(self.ser_handle.inWaiting())
                self.queue.put(text)
            time.sleep(0.2) 


class GUI:
    def __init__(self, master):
        self.master = master
        self.main_title = Label(self.master, text="Speech-To-Text Application")
        self.main_title.pack(side=TOP)
        
        # input frame
        self.input_fr = Frame(self.master)
        self.input_fr.pack(side=TOP)

        self.meeting_name_lbl = Label(self.input_fr, text="Meeting name:")
        self.meeting_name_lbl.pack(side=TOP)

        self.meeting_name = StringVar()
        self.meeting_name_inp = Entry(self.input_fr, width=50, textvariable=self.meeting_name)
        self.meeting_name_inp.pack(side=TOP)

        self.start_btn = Button(self.input_fr, text='Start transcribe',
                                command=self.start_transcribe)
        self.start_btn.pack(side=BOTTOM)

        # result frame
        self.result_fr = Frame(self.master)
        self.result_fr.pack(side=TOP)

        self.live_trans_lbl = Label(self.result_fr, text='Live transcription')
        self.live_trans_lbl.pack(side=TOP)

        self.live_trans_txt = Text(self.result_fr,
                                    width=100, height=5,
                                    wrap=WORD)
        self.live_trans_txt.pack(side=TOP)

        self.transcript_lbl = Label(self.result_fr, text='Meeting content')
        self.transcript_lbl.pack(side=TOP)

        self.transcript_txt = Text(self.result_fr,
                                    width=100, height=20,
                                    wrap=WORD)
        self.transcript_txt.pack(side=TOP)


    def start_transcribe(self):
        threading.Thread(target=self.audio_transcribe, daemon=True).start()


    def audio_transcribe(self):
        """start bidirectional streaming from microphone input to speech API"""
        mtg_name = self.meeting_name_inp.get()

        # self.transcript_txt.configure(state=NORMAL)
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

        self.transcript_txt.insert(
            END, f'\n{mtg_name} - Start recording.\n')
        # self.transcript_txt.insert(END, "End (ms)       Transcript Results/Status\n")
        self.transcript_txt.insert(
            END, "=====================================================")
        self.transcript_txt.update_idletasks()
        self.live_trans_txt.update_idletasks()

        with mic_manager as stream:

            while not stream.closed:

                self.transcript_txt.insert(
                    END, "\n" + str(STREAMING_LIMIT * stream.restart_counter) + f"{mtg_name} contents\n")
                self.transcript_txt.update_idletasks()

                stream.audio_input = []
                audio_generator = stream.generator()

                requests = (
                    speech.StreamingRecognizeRequest(audio_content=content)
                    for content in audio_generator
                )

                responses = client.streaming_recognize(streaming_config, requests)

                # Now, put the transcription responses to use.
                a = self.listen_print_loop(responses, stream)
                self.transcript_txt.insert(END, str(a))
                self.transcript_txt.update_idletasks()
                # self.transcript_txt.configure(state=DISABLED)
                if stream.result_end_time > 0:
                    stream.final_request_end_time = stream.is_final_end_time
                stream.result_end_time = 0
                stream.last_audio_input = []
                stream.last_audio_input = stream.audio_input
                stream.audio_input = []
                stream.restart_counter = stream.restart_counter + 1

                if not stream.last_transcript_was_final:

                    self.transcript_txt.insert(END, "\n")
                    self.transcript_txt.update_idletasks()

                stream.new_stream = True


    def listen_print_loop(self, responses, stream):  # convert voice into text print the data
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
                print('FINAL - ', transcript)
                self.transcript_txt.insert(
                    END, "\n" + str(corrected_time) + ": " + transcript)
                self.transcript_txt.see(END)
                self.live_trans_txt.update_idletasks()

                self.transcript_txt.update_idletasks()

                stream.is_final_end_time = stream.result_end_time
                stream.last_transcript_was_final = True

                # Exit recognition if any of the transcribed phrases could be
                # one of our keywords.
                if re.search(r"\b(exit|quit)\b", transcript, re.I):
                    self.transcript_txt.insert(END, "\nExiting...\n")
                    self.transcript_txt.update_idletasks()

                    stream.closed = True
                    break

            else:
                self.live_trans_txt.delete("1.0", "end")
                self.live_trans_txt.insert(
                    END, "\033" + str(corrected_time) + ": " + transcript + "\r")
                self.live_trans_txt.update_idletasks()
                self.transcript_txt.update_idletasks()

                stream.last_transcript_was_final = False

root = Tk()
main_ui = GUI(root)
root.mainloop()