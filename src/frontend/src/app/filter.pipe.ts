import { Pipe, PipeTransform } from '@angular/core';

@Pipe({
  name: 'filter'
})
export class FilterPipe implements PipeTransform {

  transform(items: any[], search: string): any {
    if (!search) {
      return items;
    }

    const results = [];
    for (const item of items) {
      if (item.original_title && item.original_title.match(RegExp(search, 'i'))) {
        results.push(item);
      } else if (item.original_name && item.original_name.match(RegExp(search, 'i'))) {
        results.push(item);
      } else if (item.name && item.name.match(RegExp(search, 'i'))) {
        results.push(item);
      }
    }
    return results;
  }
}
