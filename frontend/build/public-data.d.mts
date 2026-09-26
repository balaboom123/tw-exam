export interface PublicData {
  feedText: string
  searchText: string
  feedFile: string
  searchFile: string
}

export function readPublicData(sourcePath: string): Promise<PublicData>
