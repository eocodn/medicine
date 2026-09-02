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
  MedicineOcrIntake?: {
    buildMedicationQueries(items: unknown): Array<{ query_id: string; text: string; node_ids: string[] }>;
    discoverMedicationRows(items: unknown, request: (path: string, options?: any) => Promise<any>): Promise<any[]>;
    normalizeOcrItems(items: unknown): any[];
    reset(): void;
    setStatus(message: string): void;
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