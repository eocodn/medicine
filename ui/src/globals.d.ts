interface Window {
  MedicineBootstrapUi?: {
    ensureReady(): Promise<void>;
    resolve(requestId: string, rawStatus: string): void;
  };
  MedicineBootstrapNative?: {
    requestAsync(requestId: string, action: string): void;
    closeApp(): void;
  };
  MedicineDialog?: {
    handleNativeBack(): boolean;
  };
  MedicineLocalApi?: {
    request(path: string, options?: any): Promise<any> | undefined;
    resolve(requestId: string, rawEnvelope: string): void;
  };
  MedicineNative?: {
    requestAsync(
      requestId: string,
      method: string,
      path: string,
      body: string,
      coalesceKey: string,
    ): void;
  };
  // MEDICINE_OCR_START
  MedicineOcrIntake?: {
    reset(): void;
  };
  // MEDICINE_OCR_END
  friendlyErrorMessage?: (message: string) => string;
}

interface Error {
  body?: unknown;
  code?: unknown;
  status?: number;
}

declare const module:
  | {
      exports?: unknown;
    }
  | undefined;