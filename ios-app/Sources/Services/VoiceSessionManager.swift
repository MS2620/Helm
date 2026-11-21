import AVFoundation
import Foundation
import Speech

@MainActor
final class VoiceSessionManager: NSObject, ObservableObject, AVAudioPlayerDelegate {
    enum State: Equatable {
        case inactive
        case listening
        case processing
        case speaking
        case pausedForFaceID
    }

    @Published private(set) var state: State = .inactive
    @Published private(set) var liveTranscript: String = ""
    @Published var voiceError: String?

    var onUtteranceFinalized: ((String) -> Void)?

    private let speechRecognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private let audioEngine = AVAudioEngine()
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private var silenceTimer: Timer?
    private var audioPlayer: AVAudioPlayer?

    func toggleHandsFree() async {
        if state == .inactive {
            await startHandsFreeSession()
        } else {
            stopHandsFreeSession()
        }
    }

    func startHandsFreeSession() async {
        voiceError = nil
        let authorized = await requestPermissions()
        guard authorized else {
            voiceError = "Microphone and Speech Recognition permissions are required for hands-free voice mode."
            state = .inactive
            return
        }
        startListening()
    }

    func stopHandsFreeSession() {
        silenceTimer?.invalidate()
        silenceTimer = nil
        stopAudioRecording()
        audioPlayer?.stop()
        audioPlayer = nil
        liveTranscript = ""
        state = .inactive
    }

    func pauseForFaceIDConfirmation() {
        guard state != .inactive else { return }
        silenceTimer?.invalidate()
        stopAudioRecording()
        state = .pausedForFaceID
    }

    func resumeAfterFaceIDIfActive() {
        if state == .pausedForFaceID {
            startListening()
        }
    }

    func startListening() {
        silenceTimer?.invalidate()
        stopAudioRecording()
        audioPlayer?.stop()
        liveTranscript = ""

        do {
            let audioSession = AVAudioSession.sharedInstance()
            try audioSession.setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker, .allowBluetooth])
            try audioSession.setActive(true, options: .notifyOthersOnDeactivation)

            let request = SFSpeechAudioBufferRecognitionRequest()
            request.shouldReportPartialResults = true
            self.recognitionRequest = request

            let inputNode = audioEngine.inputNode
            let recordingFormat = inputNode.outputFormat(forBus: 0)
            inputNode.removeTap(onBus: 0)
            inputNode.installTap(onBus: 0, bufferSize: 1024, format: recordingFormat) { [weak self] buffer, _ in
                self?.recognitionRequest?.append(buffer)
            }

            audioEngine.prepare()
            try audioEngine.start()
            state = .listening

            recognitionTask = speechRecognizer?.recognitionTask(with: request) { [weak self] result, error in
                guard let self else { return }
                Task { @MainActor in
                    if let result {
                        self.liveTranscript = result.bestTranscription.formattedString
                        self.resetSilenceTimer()
                    }
                    if error != nil && self.state == .listening {
                        self.commitUtteranceIfNeeded()
                    }
                }
            }
        } catch {
            voiceError = "Could not start microphone: \(error.localizedDescription)"
            state = .inactive
        }
    }

    func playElevenLabsAudio(data: Data, resumeListeningAfter: Bool) {
        stopAudioRecording()
        do {
            let audioSession = AVAudioSession.sharedInstance()
            try audioSession.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker, .allowBluetooth])
            try audioSession.setActive(true)

            let player = try AVAudioPlayer(data: data)
            player.delegate = self
            self.audioPlayer = player
            state = .speaking
            player.play()
        } catch {
            voiceError = "Audio playback failed: \(error.localizedDescription)"
            if resumeListeningAfter {
                startListening()
            } else {
                state = .inactive
            }
        }
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in
            if self.state == .speaking {
                self.startListening()
            }
        }
    }

    private func resetSilenceTimer() {
        silenceTimer?.invalidate()
        silenceTimer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: false) { [weak self] _ in
            Task { @MainActor in
                self?.commitUtteranceIfNeeded()
            }
        }
    }

    private func commitUtteranceIfNeeded() {
        silenceTimer?.invalidate()
        let finalUtterance = liveTranscript.trimmingCharacters(in: .whitespacesAndNewlines)
        stopAudioRecording()

        guard !finalUtterance.isEmpty else {
            if state == .listening {
                startListening()
            }
            return
        }

        state = .processing
        liveTranscript = ""
        onUtteranceFinalized?(finalUtterance)
    }

    private func stopAudioRecording() {
        if audioEngine.isRunning {
            audioEngine.stop()
            audioEngine.inputNode.removeTap(onBus: 0)
        }
        recognitionRequest?.endAudio()
        recognitionRequest = nil
        recognitionTask?.cancel()
        recognitionTask = nil
    }

    private func requestPermissions() async -> Bool {
        let speechStatus = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status == .authorized)
            }
        }
        guard speechStatus else { return false }

        let micStatus = await AVAudioApplication.requestRecordPermission()
        return micStatus
    }
}
