interface Window {
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
  MedicineOcrIntake?: {
    reset(): void;
  };
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